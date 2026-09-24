#!/usr/bin/env bash
# 通过国内镜像站拉取 WeKnora 标准版所需镜像，并 retag 回官方名字。
#
# 背景：本机直连 registry-1.docker.io 超时；实测吞吐
#   docker.m.daocloud.io 约 1.35 MB/s（最快）
#   docker.1ms.run       约 0.20 MB/s
#   Clash 代理           约 0.20 MB/s
# 故按「daocloud → 1ms.run」顺序尝试，成功即 retag 成官方名，供 docker compose 直接使用。
#
# 拉取范围 = 默认 profile + neo4j（图谱/GraphRAG 必需）。
# 不含 qdrant/milvus/weaviate/doris（与 paradedb 互斥的其它向量库）、langfuse（观测）、dex（SSO）。
set -u

MIRRORS=(docker.m.daocloud.io docker.1ms.run)
IMAGES=(
  "wechatopenai/weknora-app:latest"
  "paradedb/paradedb:v0.22.2-pg17"
  "redis:7.0-alpine"
  "busybox:1.36"
  "neo4j:2025.10.1"
  "minio/minio:RELEASE.2025-09-07T16-13-09Z"
  "searxng/searxng:latest"
  "wechatopenai/weknora-ui:latest"
  "wechatopenai/weknora-sandbox:latest"
  "wechatopenai/weknora-docreader:latest"
)

fail=0
for img in "${IMAGES[@]}"; do
  if docker image inspect "$img" >/dev/null 2>&1; then
    echo "[skip] $img 已存在"
    continue
  fi
  ok=0
  for m in "${MIRRORS[@]}"; do
    echo "[pull] $m/$img"
    if docker pull "$m/$img"; then
      docker tag "$m/$img" "$img"
      echo "[ok]   $img  ← $m"
      ok=1
      break
    fi
    echo "[fail] $m/$img 换下一个镜像源"
  done
  if [ "$ok" -eq 0 ]; then
    echo "[ERROR] $img 所有镜像源都失败"
    fail=$((fail+1))
  fi
done

echo
echo "=== 结果 ==="
docker images --format '{{.Repository}}:{{.Tag}}  {{.Size}}' | grep -Ei 'weknora|paradedb|neo4j|redis|busybox|minio|searxng' || true
echo "失败 ${fail} 个"
exit $fail
