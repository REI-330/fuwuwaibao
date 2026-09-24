#!/usr/bin/env bash
# 启动本地 TEI（Hugging Face Text Embeddings Inference），作为 WeKnora 的向量模型后端。
# 服务 Qwen3-Embedding-0.6B，暴露 OpenAI 兼容接口 http://127.0.0.1:8090/v1/embeddings。
#
# ---------------------------------------------------------------------------
# 为什么不用 TEI 自带的模型下载器
# ---------------------------------------------------------------------------
# TEI 内置的 hf-hub 在 HF_ENDPOINT=https://hf-mirror.com 下会因响应缺少 ETag 头而直接失败：
#     WARN  Download failed: Header etag is missing
#     Error: Could not download model artifacts / Caused by: Header etag is missing
# 宿主机上的 huggingface_hub 0.30.2 走同一个镜像也会报
#     FileMetadataError: Distant resource does not seem to be on huggingface.co
# 所以本脚本假定模型权重已由宿主机预先落地（容器内全程离线，HF_HUB_OFFLINE=1）。
#
# 一次性落地模型（宿主机）——12 个文件，只有 model.safetensors 是大件：
#   DIR=/c/Users/hr206/.cache/weknora-models/Qwen3-Embedding-0.6B
#   mkdir -p "$DIR/1_Pooling"
#   aria2c -x 16 -s 16 -k 1M -d "$DIR" -o model.safetensors \
#     https://hf-mirror.com/Qwen/Qwen3-Embedding-0.6B/resolve/main/model.safetensors   # 实测约 10MiB/s
#   for f in .gitattributes 1_Pooling/config.json README.md config.json \
#            config_sentence_transformers.json generation_config.json merges.txt \
#            modules.json tokenizer.json tokenizer_config.json vocab.json; do
#     curl -sL --fail -o "$DIR/$f" \
#       "https://hf-mirror.com/Qwen/Qwen3-Embedding-0.6B/resolve/main/$f"
#   done
#   # 预期 model.safetensors = 1191586416 字节
#
# ---------------------------------------------------------------------------
# 三个必须记住的坑（改本脚本前先读）
# ---------------------------------------------------------------------------
# 1) MSYS 路径转换。Git Bash 会把 -v 的容器侧路径和 --model-id 里的 /models 改写成
#    D:/Git/models 这类 Windows 路径（等于 Git for Windows 的安装根），容器内自然找不到模型，
#    TEI 会退回去走下载逻辑。必须 MSYS_NO_PATHCONV=1，且挂载源用 C:/... 形式。
# 2) warmup 批量。--max-batch-tokens 16384 会让 TEI 用 16k token 预热，CPU 上表现为
#    「日志停在 Warming up model，约 30 秒后静默退出（ExitCode 0，无 panic）」，在
#    restart 策略下就是无限重启。2048 可用。
# 3) 自洽性校验。--max-batch-tokens 小于模型 max_input_length(32768) 时必须同时给
#    --auto-truncate，否则 TEI 直接报错退出：
#    "`--max-batch-tokens` cannot be lower than the model `max_input_length`"。
#    副作用：--auto-truncate 会把单请求 token 上限压到 max-batch-tokens。
#
# 另注：本模型 hidden_size=1024，输出已 L2 归一化。维度变更必须同步改
# config/builtin_models.yaml 的 embedding_parameters.dimension 并重建向量库。
set -euo pipefail
export MSYS_NO_PATHCONV=1

CONTAINER="${CONTAINER:-qwen-embedding}"
IMAGE="${IMAGE:-ghcr.io/huggingface/text-embeddings-inference:cpu-1.8}"
HOST_PORT="${HOST_PORT:-8090}"
# 挂载源必须是 C:/... 形式（见坑 1）
WIN_MODEL_ROOT="${WIN_MODEL_ROOT:-C:/Users/hr206/.cache/weknora-models}"
MODEL_SUBDIR="${MODEL_SUBDIR:-Qwen3-Embedding-0.6B}"
MAX_BATCH_TOKENS="${MAX_BATCH_TOKENS:-2048}"
# 默认 tokenization_workers 会取到 CPU 核数（本机 32），对单机部署偏大，压低更稳。
TOKENIZATION_WORKERS="${TOKENIZATION_WORKERS:-4}"
MAX_CONCURRENT_REQUESTS="${MAX_CONCURRENT_REQUESTS:-64}"

die() { echo "ERROR: $*" >&2; exit 1; }

command -v docker >/dev/null || die "docker 不在 PATH 中"
docker info >/dev/null 2>&1 || die "Docker daemon 未运行，先启动 Docker Desktop"

# 用 docker run 看容器内实际内容，避免被宿主路径假象误导
echo "== 检查宿主机模型文件 =="
LOCAL_DIR="/$(echo "$WIN_MODEL_ROOT" | sed 's|^\([A-Za-z]\):/|\L\1/|')/$MODEL_SUBDIR"
for f in config.json model.safetensors tokenizer.json tokenizer_config.json; do
  [ -s "$LOCAL_DIR/$f" ] || die "缺少模型文件 $LOCAL_DIR/$f（见脚本头部的一次性落地步骤）"
done
echo "   OK: $LOCAL_DIR"
echo "   model.safetensors = $(stat -c%s "$LOCAL_DIR/model.safetensors") 字节（预期 1191586416）"

echo "== 重建容器 $CONTAINER =="
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$CONTAINER" \
  --restart unless-stopped \
  -p "127.0.0.1:${HOST_PORT}:80" \
  -v "${WIN_MODEL_ROOT}:/models:ro" \
  -e HF_HUB_OFFLINE=1 \
  -e "RAYON_NUM_THREADS=${RAYON_NUM_THREADS:-8}" \
  "$IMAGE" \
  --model-id "/models/${MODEL_SUBDIR}" \
  --pooling last-token \
  --auto-truncate \
  --max-batch-tokens "$MAX_BATCH_TOKENS" \
  --tokenization-workers "$TOKENIZATION_WORKERS" \
  --max-concurrent-requests "$MAX_CONCURRENT_REQUESTS" >/dev/null

# 断言挂载与 model-id 真的生效（坑 1 的回归检查）
docker inspect "$CONTAINER" --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{end}}' \
  | grep -q "/models" || die "挂载未生效，检查 MSYS_NO_PATHCONV"
docker exec "$CONTAINER" test -s "/models/${MODEL_SUBDIR}/config.json" \
  || die "容器内看不到模型，多半是 MSYS 路径改写（见坑 1）"

echo "== 等待 /health（CPU 加载 0.6B 权重约需 30-60 秒）=="
for _ in $(seq 1 60); do
  if [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:${HOST_PORT}/health" || true)" = "200" ]; then
    echo "   health OK"; break
  fi
  sleep 3
done
[ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${HOST_PORT}/health" || true)" = "200" ] \
  || { docker logs "$CONTAINER" 2>&1 | tail -20; die "TEI 未能就绪"; }

echo "== /info =="
curl -s "http://127.0.0.1:${HOST_PORT}/info"; echo

echo "== 向量维度实测（OpenAI 兼容接口）=="
python - <<'PY'
import json, os, urllib.request
port = os.environ.get("HOST_PORT", "8090")
body = json.dumps({"input": ["维度自检"], "model": "Qwen3-Embedding-0.6B"}).encode()
req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/embeddings", data=body,
                            headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=120) as r:
    d = json.load(r)
dim = len(d["data"][0]["embedding"])
print(f"   dim={dim}")
if dim != 1024:
    raise SystemExit(f"维度 {dim} != 1024，必须同步改 builtin_models.yaml 并重建向量库")
print("   OK: 与 builtin_models.yaml 中 dimension: 1024 一致")
PY

echo
echo "TEI 就绪：http://127.0.0.1:${HOST_PORT}/v1/embeddings"
echo "容器内 WeKnora 请使用 http://host.docker.internal:${HOST_PORT}/v1（已实测可访问宿主回环端口）"
