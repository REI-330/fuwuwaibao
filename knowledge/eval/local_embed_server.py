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
DEFAULT_ONNX_DIR = "C:/Users/hr206/.cache/qwen3-embed-onnx"
DEFAULT_ONNX_MAX_LEN = 512

_model = None
_lock = threading.Lock()


class OnnxEmbedder:
    """Qwen3-Embedding-0.6B 的 ONNX 后端（1024 维），对齐旧 TEI 的 pooling=last_token。

    为什么要有这个后端：TEI 官方运行时在本机跑不动（Docker 大镜像层经代理 0.2MB/s），
    而 fastembed 的注册表里没有 Qwen3-Embedding。ONNX 版能同时满足
    「同一个模型」「1024 维」这两个可比性要求。
    与旧 TEI 的差异：权重为 int8 量化（TEI 用的是 fp32），且这里把输入截断到
    max_len 个 token（旧 TEI 的 max_batch_tokens 是 2048）。报告里必须注明。
    """

    def __init__(self, model_path: str, tokenizer_path: str, max_len: int) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.max_len = max_len
        self.tok = Tokenizer.from_file(tokenizer_path)
        self.tok.enable_truncation(max_length=max_len)
        self.tok.enable_padding(pad_id=0, pad_token="<|endoftext|>")
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = ort.InferenceSession(model_path, sess_options=opts, providers=["CPUExecutionProvider"])
        self.input_names = {i.name for i in self.sess.get_inputs()}
        self._input_specs = [(i.name, i.shape) for i in self.sess.get_inputs()]
        self.output_names = [o.name for o in self.sess.get_outputs()]

    def embed(self, texts):
        import numpy as np

        encodings = self.tok.encode_batch(list(texts))
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        batch, seq_len = input_ids.shape
        feeds = {"input_ids": input_ids, "attention_mask": attention}
        if "token_type_ids" in self.input_names:
            feeds["token_type_ids"] = np.zeros_like(input_ids)
        # 这个导出图是按「带 KV 缓存」的形态导的：position_ids 与 28 层的
        # past_key_values.*.key/value 都是**必填**输入，做单次前向时要喂空缓存。
        if "position_ids" in self.input_names:
            feeds["position_ids"] = np.tile(np.arange(seq_len, dtype=np.int64), (batch, 1))
        for spec in self._input_specs:
            name, shape = spec
            if not name.startswith("past_key_values."):
                continue
            heads = shape[1] if isinstance(shape[1], int) else 8
            head_dim = shape[3] if isinstance(shape[3], int) else 128
            feeds[name] = np.zeros((batch, heads, 0, head_dim), dtype=np.float32)
        hidden = self.sess.run(["last_hidden_state"], feeds)[0]   # (batch, seq, hidden)
        # last-token 池化：取每条序列**最后一个非 padding** 位置的向量
        last_idx = attention.sum(axis=1) - 1
        rows = np.arange(hidden.shape[0])
        vecs = hidden[rows, last_idx, :]
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (vecs / norms).astype("float32").tolist()


class FastEmbedEmbedder:
    def __init__(self, name: str) -> None:
        from fastembed import TextEmbedding

        self.name = name
        self.model = TextEmbedding(model_name=name)

    def embed(self, texts):
        import numpy as np

        out = []
        for vec in self.model.embed(list(texts)):
            arr = np.asarray(vec, dtype="float32")
            norm = float(np.linalg.norm(arr)) or 1.0
            out.append((arr / norm).tolist())
        return out


def get_embedder(backend: str, model: str, onnx_dir: str, max_len: int):
    """延迟加载：/health 立刻可用，第一次请求时才真正载入权重。"""
    global _model
    with _lock:
        if _model is None:
            if backend == "onnx":
                import os

                mp = os.path.join(onnx_dir, "model_quantized.onnx")
                tp = os.path.join(onnx_dir, "tokenizer.json")
                print(f"[embed] 载入 ONNX 模型 {mp}（max_len={max_len}）…", flush=True)
                _model = OnnxEmbedder(mp, tp, max_len)
                print("[embed] ONNX 模型就绪", flush=True)
            else:
                print(f"[embed] 载入 fastembed 模型 {model} …", flush=True)
                _model = FastEmbedEmbedder(model)
                print("[embed] fastembed 模型就绪", flush=True)
        return _model


class Handler(BaseHTTPRequestHandler):
    server_version = "local-embed/1.0"
    protocol_version = "HTTP/1.1"
    model_name = DEFAULT_MODEL
    backend = "fastembed"
    onnx_dir = DEFAULT_ONNX_DIR
    max_len = DEFAULT_ONNX_MAX_LEN
    dim = 0

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
            self._send(200, {"status": "ok", "backend": self.backend, "model": self.model_name, "dim": self.dim or None})
        elif path == "/info":
            # 按 TEI 的 /info 形状返回，评测脚本会读 model_id / model_dtype 打印一行配置。
            self._send(200, {
                "model_id": self.model_name,
                "model_dtype": "int8" if self.backend == "onnx" else "float32",
                "model_type": {"embedding": {"pooling": "last_token" if self.backend == "onnx" else "cls"}},
                "max_input_length": self.max_len if self.backend == "onnx" else 512,
                "max_batch_tokens": self.max_len if self.backend == "onnx" else 512,
                "max_batch_requests": 4,
                "max_concurrent_requests": 64,
                "tokenization_workers": 4,
                "dim": self.dim or None,
                "backend": f"local-embed/{self.backend}",
                "note": "本地嵌入服务，接口与 TEI 兼容；非 TEI 官方运行时。",
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
            vectors = get_embedder(self.backend, self.model_name, self.onnx_dir, self.max_len).embed([str(t) for t in texts])
            if vectors and not Handler.dim:
                Handler.dim = len(vectors[0])
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
    parser.add_argument("--backend", choices=["fastembed", "onnx"], default="fastembed",
                        help="fastembed=按模型名走注册表；onnx=本地 ONNX 权重（Qwen3-Embedding-0.6B，1024 维）")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="fastembed 模型名，或 onnx 后端下的展示名")
    parser.add_argument("--onnx-dir", default=DEFAULT_ONNX_DIR, help="onnx 后端的模型目录（需含 model_quantized.onnx 与 tokenizer.json）")
    parser.add_argument("--max-len", type=int, default=DEFAULT_ONNX_MAX_LEN, help="onnx 后端的输入截断长度（token）")
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
    Handler.backend = args.backend
    Handler.onnx_dir = args.onnx_dir
    Handler.max_len = args.max_len
    if args.backend == "onnx":
        Handler.model_name = args.model if args.model != DEFAULT_MODEL else "Qwen3-Embedding-0.6B(int8,onnx)"
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[embed] 监听 http://{args.host}:{args.port}/v1/embeddings  backend={args.backend} model={Handler.model_name}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
