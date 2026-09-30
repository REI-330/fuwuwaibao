"""评测侧的 LLM 入口：**与产品侧共用同一份实现与纪律**。

为什么要有这一层：本项目原先 9 个评测脚本各自手写了一遍 `def llm(...)`（无一例外是
`urllib` + `DEEPEVAL_*` + 自己的 max_tokens），产品侧的记忆触发器又要用同一个端点。
两份并行代码的代价是：修一处纪律（比如"推理模型会把额度吃在思维链上"）改不到另一处。
所以评测脚本统一改为 `from _shared_llm import chat`。

三条纪律由 `backend/llm.py` 统一保证（都是踩过的坑，别再各自实现一遍）：

* `max_tokens` 有下限 —— 端点的 `deepseek-v4.1-flash` 是推理模型，
  给 200 时常出现 `reasoning_tokens≈额度`、`content` 回空串（HTTP 200 但什么都没答）；
* 瞬时失败（429/5xx/超时）退避重试，**耗尽后抛出**，绝不返回空串让上层静默退化；
* 4xx（429 除外）不重试 —— 配置错误重试三次只是把一次错误拖成三倍等待。

用法（脚本里保持原来的函数签名，只把函数体换掉）：

    from _shared_llm import chat
    def llm(prompt, timeout=180, max_tokens=1500): return chat(prompt, ...)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

_BACKEND_ROOT: Optional[Path] = None


def backend_root() -> Path:
    """定位「含 backend/ 包的那个目录」，并把它加进 `sys.path`。

    本仓库的 `backend/` 不在仓库根，而是在 `frontend/frotent/frontend1/backend/`；
    所以这里按「先看 frontend/frotent/frontend1，再看祖先目录」两种布局都试一遍，
    而不是想当然地把仓库根塞进 sys.path（那样会 `ModuleNotFoundError: backend`）。
    """
    global _BACKEND_ROOT
    if _BACKEND_ROOT is None:
        here = Path(__file__).resolve()
        for base in here.parents:
            for candidate in (base / "frontend" / "frotent" / "frontend1", base):
                if (candidate / "backend" / "llm.py").is_file():
                    _BACKEND_ROOT = candidate
                    break
            if _BACKEND_ROOT is not None:
                break
        if _BACKEND_ROOT is None:
            raise RuntimeError(
                f"找不到 backend/llm.py（从 {here} 向上找过 frontend/frotent/frontend1 与各级祖先目录）"
            )
        if str(_BACKEND_ROOT) not in sys.path:
            sys.path.insert(0, str(_BACKEND_ROOT))
    return _BACKEND_ROOT


backend_root()  # 先确保 backend 可导入

from backend.llm import (  # noqa: E402 —— 必须在 sys.path 调整之后导入
    MIN_MAX_TOKENS,
    LlmClient,
    LlmConfig,
    LlmError,
    load_config,
)

__all__ = ["chat", "client", "load_config", "describe", "LlmError", "LlmClient", "LlmConfig", "MIN_MAX_TOKENS"]

_CLIENT: Optional[LlmClient] = None


def client() -> LlmClient:
    """进程内单例：配置只解析一次，便于脚本末尾打印模型名。"""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = LlmClient(load_config())
    return _CLIENT


def describe() -> dict:
    return client().describe()


def chat(
    prompt: str,
    *,
    max_tokens: int = 1500,
    timeout: int = 180,
    attempts: int = 3,
    temperature: float = 0.0,
    backoff: tuple = (1.0, 8.0),
) -> str:
    """发一次补全并返回正文。失败抛 `LlmError`（脚本应让它冒出去，评测失败即中止）。

    `max_tokens` 缺省 1500 而不是原脚本里常见的 200–500：那几个值对现役推理模型
    已经不够用（思维链吃额度，正文会回空串），迁过来时一并抬到安全区。
    """
    return client().chat(
        prompt,
        max_tokens=max_tokens,
        timeout=timeout,
        attempts=attempts,
        temperature=temperature,
        backoff=backoff,
    )
