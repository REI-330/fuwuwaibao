#!/usr/bin/env bash
# 下载 bge-reranker-v2-m3（WeKnora 文档推荐的 rerank 模型）到本地模型缓存目录。
#
# 背景：huggingface.co 直连超时；hf-mirror.com 可达且快。用 resolve 接口逐文件下，
# 带断点续传（-C -），中断后重跑即可继续。
set -u

DEST="${1:-C:/Users/hr206/.cache/weknora-models/bge-reranker-v2-m3}"
REPO="BAAI/bge-reranker-v2-m3"
BASE="https://hf-mirror.com/${REPO}/resolve/main"

FILES=(
  config.json
  tokenizer.json
  tokenizer_config.json
  special_tokens_map.json
  sentencepiece.bpe.model
  model.safetensors
)

mkdir -p "$DEST"
echo "目标目录：$DEST"
fail=0
for f in "${FILES[@]}"; do
  out="$DEST/$f"
  if [ -s "$out" ]; then
    echo "[skip] $f 已存在（$(du -h "$out" | cut -f1)）"
    continue
  fi
  echo "[get ] $f"
  if curl -L --fail --retry 3 --retry-delay 5 -C - \
        --connect-timeout 20 --max-time 3600 \
        -o "$out" "$BASE/$f"; then
    echo "[ok  ] $f  $(du -h "$out" | cut -f1)"
  else
    echo "[FAIL] $f"
    fail=$((fail+1))
  fi
done

echo
echo "=== 结果 ==="
ls -la "$DEST"
echo "失败 ${fail} 个"
exit $fail
