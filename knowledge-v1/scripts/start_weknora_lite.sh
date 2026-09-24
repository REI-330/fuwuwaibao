#!/usr/bin/env bash
# 启动 WeKnora（Lite 单进程版），并把向量模型指向宿主机 TEI。
#
# 为什么用 Lite：本机 Docker Hub 不可达（DNS 被污染 + 直连被墙），标准版的 5 个镜像
# 共 2.72GB 拉不动。Lite 是单二进制、零依赖（sqlite + FTS5 + sqlite-vec），无需镜像。
#
# 三个必须记住的坑（都是实测踩出来的）：
#
# 1) 必须显式 source .env.lite
#    exe 本身不会自动加载该文件。不加载时 DB_DRIVER 为空，启动直接 panic：
#      panic: unsupported database driver:
#    官方发行包的启动脚本同样是 set -a && source .env.lite。
#
# 2) SSRF_WHITELIST 必须包含 127.0.0.1
#    向量模型跑在宿主机 TEI（http://127.0.0.1:8090/v1），而 WeKnora 的 SSRF 防护默认
#    拦截本机与私网地址。不加白名单的表现极具迷惑性：解析、分块、模型查找全部成功，
#    只有构造 embedding 客户端时报
#      base URL SSRF check failed: SSRF validation failed: hostname 127.0.0.1 is restricted
#    → "processChunks get embedding model failed" → 文档永久停在 processing。
#
# 3) embedding_model_id 只能在**创建知识库时**指定
#    PUT /knowledge-bases/:id 更新该字段会返回 200 但被静默忽略，之后同样落到上面的
#    processChunks 失败。所以知识库必须带 embedding_model_id 创建。
#
# 另注：重启前务必确认旧进程真的退出了。只杀 bash 包装进程会留下 orphan 的
# WeKnora-lite.exe 继续占用 8080，新实例会以
#   listen tcp 0.0.0.0:8080: bind: Only one usage of each socket address
# 失败，而请求仍由没有新配置的旧实例处理——排查时极易误判。
set -euo pipefail

DIR="$(cd "$(dirname "$0")/../weknora-src" && pwd)"
cd "$DIR"

[ -f WeKnora-lite.exe ] || { echo "ERROR: 找不到 WeKnora-lite.exe（应在 $DIR）" >&2; exit 1; }
[ -f .env.lite ] || { echo "ERROR: 找不到 .env.lite" >&2; exit 1; }

# 清理可能残留的旧实例（见坑 3 的注释）
if tasklist //NH 2>/dev/null | grep -qi "weknora-lite"; then
  echo "== 发现残留的 WeKnora-lite 进程，正在结束 =="
  powershell -NoProfile -Command "Get-Process WeKnora-lite -ErrorAction SilentlyContinue | Stop-Process -Force" || true
  sleep 1
fi

echo "== 加载 .env.lite =="
set -a
# shellcheck disable=SC1091
. ./.env.lite
set +a
echo "   DB_DRIVER=$DB_DRIVER"
echo "   RETRIEVE_DRIVER=$RETRIEVE_DRIVER"
echo "   SSRF_WHITELIST=${SSRF_WHITELIST:-<未设置 → 向量模型会被 SSRF 拦截>}"

echo "== 启动 WeKnora Lite（监听 0.0.0.0:8080）=="
exec ./WeKnora-lite.exe
