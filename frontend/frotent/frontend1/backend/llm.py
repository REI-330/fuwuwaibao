"""LLM 客户端（Python 标准库，零三方依赖）。

**为什么这个文件同时服务「产品」与「评测」两件事。** 本项目此前只有评测侧用到模型
（重排档 / 裁判档 / 查询扩展），配置散在 `knowledge/eval/.env` 的 `DEEPEVAL_*` 里；
记忆库的写时触发器原本是规则版，接上模型后就需要同一个端点。于是这里做一件事：
**一处配置、两处消费** —— 产品侧（`backend/memories.py` 的触发器）与评测侧
（`knowledge/eval/*.py`）读同一组变量，改一个地方两边都变。

配置解析顺序（先命中先用）：
1. `CAREER_LLM_BASE_URL` / `CAREER_LLM_API_KEY` / `CAREER_LLM_MODEL`（环境变量，产品侧显式覆盖用）
2. `DEEPEVAL_BASE_URL` / `DEEPEVAL_API_KEY` / `DEEPEVAL_MODEL`（环境变量或 `knowledge/eval/.env`）
3. 都没有 → `load_config()` 返回 None，调用方必须**降级并如实标注**，不许假装有模型

三条从评测侧踩坑换来的硬约束，这里直接内建（注释里保留了出处）：

* **`max_tokens` 必须有下限**。端点现役的 `deepseek-v4.1-flash` 是推理模型，思维链与正文
  共用这个额度。实测 `max_tokens=50` 时 `completion_tokens=50 / reasoning_tokens=50`、
  `content` 回空串 —— 表面 HTTP 200，实际什么都没答。这里给下限并在检测到该形态时
  **明确报错**，而不是返回空字符串让上层静默退化成"用原问题"。
* **必须退避重试**。端点对突发调用会直接拒（评测脚本注释记录：连打 30+ 次后大量 HTTPError）。
  失败被吞掉会让实验"跑完了但基线=实验组"，所以这里只在耗尽重试后抛出。
* **密钥绝不回显**。`describe()` 只给出 host 与模型名，用于 `/health` 与证据文件。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ENV_KEYS = ("CAREER_LLM", "DEEPEVAL")
ENV_FILE_RELATIVE = "knowledge/eval/.env"

# 推理模型的思维链会吃额度：低于这个数就可能出现「HTTP 200 但 content 为空」
MIN_MAX_TOKENS = 512
DEFAULT_MAX_TOKENS = 2048
# 空正文 + 额度被吃光时自动翻倍的上限（再大就纯属浪费；实测 4096–8192 足够出正文）
MAX_MAX_TOKENS = 16384
DEFAULT_TIMEOUT = 180
DEFAULT_ATTEMPTS = 3


class LlmError(RuntimeError):
    """带错误码的 LLM 失败。上层据此决定降级方式，不要凭 message 猜。"""

    def __init__(self, code: str, message: str, detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


@dataclass(frozen=True)
class LlmConfig:
    base_url: str
    api_key: str
    model: str
    # 配置来自哪里（`env:CAREER_LLM` / `env:DEEPEVAL` / `file:<path>`），便于排查"为什么没生效"
    source: str

    @property
    def host(self) -> str:
        """只暴露 host，用于日志与 /health —— 不泄漏密钥，也不泄漏内网路径。"""
        try:
            from urllib.parse import urlparse

            parsed = urlparse(self.base_url)
            return f"{parsed.scheme}://{parsed.hostname}" + (f":{parsed.port}" if parsed.port else "")
        except Exception:  # noqa: BLE001
            return self.base_url


def repo_root_from(start: Path) -> Optional[Path]:
    """从当前文件向上找仓库根（以 `knowledge/exports` 或 `knowledge/eval` 为标志）。"""
    for base in Path(start).resolve().parents:
        if (base / "knowledge" / "eval").is_dir() or (base / "knowledge" / "exports").is_dir():
            return base
    return None


def find_env_file() -> Optional[Path]:
    candidates = []
    override = os.environ.get("CAREER_LLM_ENV_FILE")
    if override:
        candidates.append(Path(override).expanduser())
    root = repo_root_from(Path(__file__))
    if root is not None:
        candidates.append(root / ENV_FILE_RELATIVE)
    candidates.append(Path.cwd() / ENV_FILE_RELATIVE)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def read_env_file(path: Path) -> Dict[str, str]:
    """按 `KEY=VALUE` 读一份 .env（不引入 python-dotenv）；已存在的环境变量优先。"""
    values: Dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_config(env: Optional[Dict[str, str]] = None) -> Optional[LlmConfig]:
    """按上面写明的顺序解析配置；解析不到返回 None。"""
    lookup = dict(os.environ) if env is None else dict(env)
    # 显式关闭开关：`CAREER_LLM_DISABLED=1` 时一律返回 None。
    # 存在的理由很实在：本机 `knowledge/eval/.env` 里有 key，单元测试与离线跑批
    # 不能被它悄悄拖去联网（会给测试引入 5–20 s 的网络等待与不稳定）。
    if str(lookup.get("CAREER_LLM_DISABLED") or "").strip().lower() in ("1", "true", "yes", "on"):
        return None
    env_file = None if env is not None else find_env_file()
    if env_file is not None:
        for key, value in read_env_file(env_file).items():
            lookup.setdefault(key, value)

    for prefix in ENV_KEYS:
        base = (lookup.get(f"{prefix}_BASE_URL") or "").strip().rstrip("/")
        api_key = (lookup.get(f"{prefix}_API_KEY") or "").strip()
        model = (lookup.get(f"{prefix}_MODEL") or "").strip()
        if base and api_key and model:
            source = f"env:{prefix}"
            if env_file is not None and f"{prefix}_API_KEY" not in os.environ and env is None:
                source = f"file:{env_file}"
            return LlmConfig(base_url=base, api_key=api_key, model=model, source=source)
    return None


Transport = Callable[[str, bytes, Dict[str, str], int], Tuple[int, str]]


def _urllib_transport(url: str, body: bytes, headers: Dict[str, str], timeout: int) -> Tuple[int, str]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:  # 4xx/5xx 也要把 body 读出来，便于定位
        return error.code, error.read().decode("utf-8", "replace")


class LlmClient:
    """OpenAI 兼容 `/chat/completions` 的最小客户端。

    `transport` 可注入：测试用假传输，绝不联网（见 `backend/tests/test_llm.py`）。
    """

    def __init__(
        self,
        config: Optional[LlmConfig] = None,
        transport: Optional[Transport] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._transport = transport or _urllib_transport
        self._sleep = sleep
        self.last_usage: Dict[str, Any] = {}

    # ------------------------------------------------------------------ 观察
    @property
    def configured(self) -> bool:
        return self.config is not None

    def describe(self) -> Dict[str, Any]:
        """给 `/health` 与证据文件用：不含密钥。"""
        if self.config is None:
            return {
                "configured": False,
                "source": None,
                "host": None,
                "model": None,
                "note": "未配置 LLM 端点：记忆触发器会走规则版，评测的重排/裁判档无法运行",
            }
        return {
            "configured": True,
            "source": self.config.source,
            "host": self.config.host,
            "model": self.config.model,
        }

    # ------------------------------------------------------------------ 调用
    def chat(
        self,
        prompt: str,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: int = DEFAULT_TIMEOUT,
        attempts: int = DEFAULT_ATTEMPTS,
        temperature: float = 0.0,
        backoff: Tuple[float, float] = (1.0, 8.0),
        escalate: bool = True,
    ) -> str:
        """发一次对话补全，返回正文。失败抛 `LlmError`（**绝不返回空串**）。

        `backoff=(基数, 上限)`：重试等待为 `min(基数 * 2**i, 上限)` 秒。
        默认 (1, 8) 适合交互式调用；批量出题的脚本会遇到持续 1–3 分钟的中断，
        需要 `(5, 40)` 这种更长的等待（原脚本各自硬编码过这些数字）。

        `escalate`：正文为空且额度被思维链吃光时，**自动把额度翻倍重试**（上限 `MAX_MAX_TOKENS`）。
        实测同一条 prompt 的 `reasoning_tokens` 在 1794–2048 之间浮动，固定额度会在
        "刚好不够"的那次失败 —— 翻倍比"原样重试"有效，比"调用方一次给够"省额度。
        """
        if self.config is None:
            raise LlmError("LLM_NOT_CONFIGURED", "未配置 LLM 端点：请设置 CAREER_LLM_* 或 DEEPEVAL_*")
        if max_tokens < MIN_MAX_TOKENS:
            # 不静默抬高：调用方可能真的以为 200 够用，日志里说清楚为什么不允许
            raise LlmError(
                "LLM_MAX_TOKENS_TOO_SMALL",
                f"max_tokens={max_tokens} 低于下限 {MIN_MAX_TOKENS}：端点的推理模型会把额度吃在思维链上，"
                "正文会回空串（实测 max_tokens=50 时 reasoning_tokens=50、content=''）",
                {"maxTokens": max_tokens, "floor": MIN_MAX_TOKENS},
            )
        base_delay, cap_delay = backoff
        budget = max_tokens
        escalations: List[Dict[str, Any]] = []

        def pause(attempt: int) -> None:
            self._sleep(min(base_delay * (2 ** attempt), cap_delay))

        def build_body(limit: int) -> bytes:
            payload = {
                "model": self.config.model,
                "temperature": temperature,
                "max_tokens": limit,
                "messages": [{"role": "user", "content": prompt}],
            }
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")

        body = build_body(budget)
        headers = {
            "Authorization": "Bearer " + self.config.api_key,
            "Content-Type": "application/json; charset=utf-8",
        }
        url = self.config.base_url + "/chat/completions"

        last: Optional[LlmError] = None
        for attempt in range(max(1, attempts)):
            started = time.time()
            try:
                status, text = self._transport(url, body, headers, timeout)
            except Exception as error:  # noqa: BLE001 —— 超时/连接错都要能重试
                last = LlmError("LLM_TRANSPORT_ERROR", f"{type(error).__name__}: {error}")
                pause(attempt)
                continue

            if status != 200:
                detail = _truncate(text, 400)
                # 4xx 里除了 429 都是调用方的问题，重试没意义
                last = LlmError("LLM_HTTP_ERROR", f"HTTP {status}：{detail}", {"status": status})
                if 400 <= status < 500 and status != 429:
                    raise last
                pause(attempt)
                continue

            try:
                parsed = json.loads(text)
                message = parsed["choices"][0]["message"]
            except (ValueError, KeyError, IndexError, TypeError) as error:
                last = LlmError("LLM_BAD_RESPONSE", f"响应不是预期结构（{type(error).__name__}）：{_truncate(text, 300)}")
                pause(attempt)
                continue

            content = (message.get("content") or "").strip()
            usage = parsed.get("usage") or {}
            reasoning = ((usage.get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0
            self.last_usage = {
                **usage,
                "elapsedMs": int((time.time() - started) * 1000),
                "maxTokens": budget,
                "escalations": escalations,
            }
            if content:
                return content

            # HTTP 200 但正文为空：推理模型的思维链吃光了额度。必须报错，
            # 否则上层会拿到空串并把"生成失败"记成"生成成功但没内容"。
            completion = usage.get("completion_tokens") or 0
            hit_cap = reasoning >= budget * 0.9 or completion >= budget * 0.9
            last = LlmError(
                "LLM_EMPTY_CONTENT",
                f"HTTP 200 但正文为空（reasoning_tokens={reasoning}，completion_tokens={completion}）："
                f"思维链吃光了 max_tokens={budget}",
                {"usage": usage, "maxTokens": budget, "floor": MIN_MAX_TOKENS},
            )
            if escalate and hit_cap and budget < MAX_MAX_TOKENS:
                # 额度不够，不是瞬时故障：翻倍重比"原样重试"有效，也比一开始就给 16k 省额度
                previous = budget
                budget = min(budget * 2, MAX_MAX_TOKENS)
                escalations.append({"from": previous, "to": budget, "reasoningTokens": reasoning})
                body = build_body(budget)
                pause(attempt)
                continue
            pause(attempt)

        raise last or LlmError("LLM_UNKNOWN", "未知的 LLM 失败")

    def list_models(self, timeout: int = 20) -> list:
        """`GET /models`：只用于连通性自检（不重试，失败即抛）。"""
        if self.config is None:
            raise LlmError("LLM_NOT_CONFIGURED", "未配置 LLM 端点")
        request = urllib.request.Request(
            self.config.base_url + "/models",
            headers={"Authorization": "Bearer " + self.config.api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except Exception as error:  # noqa: BLE001
            raise LlmError("LLM_TRANSPORT_ERROR", f"{type(error).__name__}: {error}") from error
        return [entry.get("id") for entry in parsed.get("data", []) if entry.get("id")]


def _truncate(text: str, limit: int) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def default_client() -> LlmClient:
    return LlmClient(load_config())
