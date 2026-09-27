#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""本地嵌入服务：用 fastembed(ONNX) 顶替 TEI，暴露与 TEI 相同的 OpenAI 兼容接口。

## 为什么需要它

评测脚本（vector_retrieval.py / rerank_experiment.py 等）通过 HTTP 调用 TEI：
    POST {TEI_EMBED_URL}   body {"input": [...], "model": "..."}
本机跑 TEI 官方路子要 Docker 镜像（历史上大镜像层拉不动）+ 1.19GB 权重。
这里用 fastembed（onnxruntime，无需 torch、无需 Docker、模型 ~95MB）提供同一接口，
脚本侧只改环境变量 `TEI_EMBED_URL` 即可，不改任何代码。

## 与 TEI 的差异（必须知道）

- **模型不同**：TEI 用的是 Qwen3-Embedding-0.6B（1024 维）；本服务默认用
  `BAAI/bge-small-zh-v1.5`（512 维，中文检索专用）。维度与旧指标不可直接比较，
  报告里必须注明用的是哪一个后端与模型。
- **向量归一化**：本服务统一做 L2 归一化后再返回（与 TEI 的 last_token+归一化行为对齐），
  使调用侧「点积即余弦」的假设成立。
- 不支持批量上限协商、不做 max_batch_tokens 切分；调用侧分批即可（脚本本来就是分批的）。

跑法：
    HF_ENDPOINT=https://hf-mirror.com .venv/Scripts/python.exe knowledge/eval/local_embed_server.py
    # 可选：--model BAAI/bge-base-zh-v1.5 --port 8090 --list-models
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"

_model = None
_lock = threading.Lock()


def get_model(name: str):
    """延迟加载：启动即可用（/health 先通），第一次请求时才真正载入权重。"""
    global _model
    with _lock:
        if _model is None:
            from fastembed import TextEmbedding

            print(f"[embed] 载入模型 {name} …", flush=True)
            _model = TextEmbedding(model_name=name)
            print("[embed] 模型就绪", flush=True)
        return _model


def embed(texts: List[str], name: str) -> List[List[float]]:
    import numpy as np

    model = get_model(name)
    vectors = []
    for vec in model.embed(texts):
        arr = np.asarray(vec, dtype="float32")
        norm = float(np.linalg.norm(arr)) or 1.0
        vectors.append((arr / norm).tolist())
    return vectors


class Handler(BaseHTTPRequestHandler):
    server_version = "local-embed/1.0"
    protocol_version = "HTTP/1.1"
    model_name = DEFAULT_MODEL

    def log_message(self, fmt: str, *args: Any) -> None:  # 保持安静，日志走 stderr 单行
        sys.stderr.write("[embed] " + (fmt % args) + "\n")

    def _send(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.rstrip("/")
        if path in ("/health", ""):
            self._send(200, {"status": "ok", "backend": "fastembed", "model": self.model_name})
        elif path == "/info":
            # 按 TEI 的 /info 形状返回，评测脚本会读 model_id / model_dtype 打印一行配置。
            self._send(200, {
                "model_id": self.model_name,
                "model_dtype": "float32",
                "model_type": {"embedding": {"pooling": "cls"}},
                "max_input_length": 512,
                "max_batch_tokens": 512,
                "max_batch_requests": 4,
                "max_concurrent_requests": 64,
                "tokenization_workers": 4,
                "backend": "fastembed-onnx",
                "note": "本地 fastembed 服务，接口与 TEI 兼容；非 TEI 官方运行时。维度 512（TEI+Qwen3-Embedding-0.6B 为 1024），指标不可与旧档直接比较。",
            })
        elif path == "/v1/models":
            self._send(200, {"data": [{"id": self.model_name, "object": "model"}]})
        else:
            self._send(404, {"error": {"message": f"未知路径 {self.path}"}})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") not in ("/v1/embeddings", "/embeddings"):
            self._send(404, {"error": {"message": f"未知路径 {self.path}"}})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as error:  # noqa: BLE001
            self._send(400, {"error": {"message": f"请求体不是 JSON：{error}"}})
            return

        texts = payload.get("input") or payload.get("inputs")
        if isinstance(texts, str):
            texts = [texts]
        if not isinstance(texts, list) or not texts:
            self._send(400, {"error": {"message": "缺少 input（字符串或字符串数组）"}})
            return

        try:
            vectors = embed([str(t) for t in texts], self.model_name)
        except Exception as error:  # noqa: BLE001
            self._send(500, {"error": {"message": f"嵌入失败：{error}"}})
            return

        self._send(200, {
            "object": "list",
            "model": self.model_name,
            "data": [{"object": "embedding", "index": i, "embedding": vec} for i, vec in enumerate(vectors)],
        })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--list-models", action="store_true", help="列出 fastembed 支持的模型后退出")
    args = parser.parse_args()

    if args.list_models:
        from fastembed import TextEmbedding

        for spec in TextEmbedding.list_supported_models():
            print(f"{spec.get('model')}  dim={spec.get('dim')}  size={spec.get('size_in_GB')}GB")
        return 0

    Handler.model_name = args.model
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[embed] 监听 http://{args.host}:{args.port}/v1/embeddings  model={args.model}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
