"""LLM 客户端与模型版触发器的测试（**不联网**）。

跑法：``python -m pytest backend/tests -q``。

这里每个用例都对应一次真实踩坑，不是凑覆盖率：
* `max_tokens=50` → `reasoning_tokens=50`、`content=""`（HTTP 200 却什么都没答）；
* 连打几十次后端点开始拒（429/5xx），不重试就会静默退化成"没扩展"；
* 4xx 重试没意义，只会把一次明确的配置错误拖成三倍等待；
* 模型常把 JSON 后面再补一段说明文字，解析必须容错。
"""

from __future__ import annotations

import json

import pytest

from backend.llm import LlmClient, LlmConfig, LlmError, load_config
from backend.memories import MemoryStore
from backend.memory_triggers import (
    build_prompt,
    generate_triggers_with_llm,
    parse_json_robust,
    validate_triggers,
)


# --------------------------------------------------------------------- 假传输


class FakeTransport:
    """按顺序回放预设响应；记录调用次数与最后一次请求体。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.bodies = []

    def __call__(self, url, body, headers, timeout):
        self.calls.append((url, timeout, headers.get("Authorization")))
        self.bodies.append(json.loads(body.decode("utf-8")))
        if not self.responses:
            raise AssertionError("FakeTransport 收到的调用次数超出预期")
        response = self.responses.pop(0)
        # 不校验的话，误传一个字符串会被解包成两个字符，失败现场看起来像"传输错误"，很难查
        assert isinstance(response, tuple) and len(response) == 2, f"响应必须是 (status, text) 元组，收到 {response!r}"
        return response


def completion(content: str, reasoning: int = 0) -> str:
    return json.dumps(
        {
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {
                "completion_tokens": reasoning + len(content),
                "completion_tokens_details": {"reasoning_tokens": reasoning},
            },
        }
    )


CONFIG = LlmConfig(base_url="https://example.invalid/v1", api_key="sk-secret-do-not-log", model="test-model", source="test")


def client(transport: FakeTransport, sleep=lambda _seconds: None) -> LlmClient:
    return LlmClient(CONFIG, transport=transport, sleep=sleep)


# --------------------------------------------------------------------- 配置


def test_config_precedence_and_disable_switch() -> None:
    both = {
        "CAREER_LLM_BASE_URL": "https://career.invalid/v1",
        "CAREER_LLM_API_KEY": "career-key",
        "CAREER_LLM_MODEL": "career-model",
        "DEEPEVAL_BASE_URL": "https://eval.invalid/v1",
        "DEEPEVAL_API_KEY": "eval-key",
        "DEEPEVAL_MODEL": "eval-model",
    }
    config = load_config(both)
    assert config is not None and config.model == "career-model" and config.source == "env:CAREER_LLM"

    eval_only = {key: value for key, value in both.items() if key.startswith("DEEPEVAL")}
    config = load_config(eval_only)
    assert config is not None and config.model == "eval-model" and config.source == "env:DEEPEVAL"

    # 缺一项就不算配好：宁可不配，也不要拿半截配置去发请求
    for missing in ("_BASE_URL", "_API_KEY", "_MODEL"):
        partial = {key: value for key, value in both.items() if key != "CAREER_LLM" + missing}
        assert load_config(partial) is None or load_config(partial).model == "eval-model"

    assert load_config({}) is None
    assert load_config({**both, "CAREER_LLM_DISABLED": "1"}) is None
    assert load_config({**both, "CAREER_LLM_DISABLED": "true"}) is None


def test_describe_never_leaks_the_key() -> None:
    description = client(FakeTransport()).describe()
    assert description["configured"] is True
    assert description["host"] == "https://example.invalid"
    assert description["model"] == "test-model"
    assert "sk-secret-do-not-log" not in json.dumps(description)

    unconfigured = LlmClient(None).describe()
    assert unconfigured["configured"] is False
    assert unconfigured["host"] is None and unconfigured["model"] is None
    assert "note" in unconfigured


# --------------------------------------------------------------------- 调用纪律


def test_unconfigured_client_raises_instead_of_returning_empty() -> None:
    with pytest.raises(LlmError) as error:
        LlmClient(None).chat("任意问题")
    assert error.value.code == "LLM_NOT_CONFIGURED"


def test_max_tokens_floor_is_enforced() -> None:
    """实测：max_tokens=50 时思维链吃光额度，content 回空串。不许静默抬高，要直接报错。"""
    transport = FakeTransport(completion("北京", reasoning=50))
    with pytest.raises(LlmError) as error:
        client(transport).chat("只回答一个词：中国首都", max_tokens=200)
    assert error.value.code == "LLM_MAX_TOKENS_TOO_SMALL"
    assert transport.calls == [], "低于下限时不该发出任何请求"


def test_http_200_with_empty_content_is_an_error_not_an_empty_answer() -> None:
    transport = FakeTransport(*((200, completion("", reasoning=512)) for _ in range(3)))
    with pytest.raises(LlmError) as error:
        client(transport).chat("问题", max_tokens=512, attempts=3)
    assert error.value.code == "LLM_EMPTY_CONTENT"
    assert error.value.detail["usage"]["completion_tokens_details"]["reasoning_tokens"] == 512
    assert len(transport.calls) == 3, "空正文属于可重试形态"


def test_empty_content_escalates_the_budget_instead_of_just_retrying() -> None:
    """实测：同一条 prompt 的 reasoning_tokens 会在 1794–2048 之间浮动，
    固定额度必然会在"刚好不够"的那次失败。翻倍重比"原样重试"有效。"""
    transport = FakeTransport(
        (200, completion("", reasoning=512)),          # 额度被吃光
        (200, completion("端侧AI部署", reasoning=300)),  # 翻倍后成功
    )
    client_instance = client(transport)
    assert client_instance.chat("问题", max_tokens=512, attempts=2) == "端侧AI部署"
    assert [body["max_tokens"] for body in transport.bodies] == [512, 1024]
    assert client_instance.last_usage["escalations"] == [{"from": 512, "to": 1024, "reasoningTokens": 512}]
    assert client_instance.last_usage["maxTokens"] == 1024


def test_escalation_stops_at_the_cap_and_still_reports_why() -> None:
    # 每次响应都报"额度基本被吃光"，逼它一路翻到上限
    transport = FakeTransport(*((200, completion("", reasoning=used)) for used in (4096, 8000, 15000)))
    with pytest.raises(LlmError) as error:
        client(transport).chat("问题", max_tokens=4096, attempts=3)
    assert error.value.code == "LLM_EMPTY_CONTENT"
    # 4096 → 8192 → 16384（上限），之后不再翻倍
    assert [body["max_tokens"] for body in transport.bodies] == [4096, 8192, 16384]


def test_retries_transient_failures_then_succeeds() -> None:
    transport = FakeTransport((429, "rate limited"), (503, "upstream busy"), (200, completion("好了")))
    assert client(transport).chat("问题") == "好了"
    assert len(transport.calls) == 3


def test_client_side_errors_fail_fast_without_retrying() -> None:
    transport = FakeTransport((401, "invalid api key"), (200, completion("不会被用到")))
    with pytest.raises(LlmError) as error:
        client(transport).chat("问题")
    assert error.value.code == "LLM_HTTP_ERROR"
    assert error.value.detail["status"] == 401
    assert len(transport.calls) == 1, "4xx（429 除外）重试只会浪费三倍时间"


def test_transport_exception_is_retried_and_reported() -> None:
    def boom(url, body, headers, timeout):
        raise TimeoutError("read timed out")

    with pytest.raises(LlmError) as error:
        LlmClient(CONFIG, transport=boom, sleep=lambda _s: None).chat("问题", attempts=2)
    assert error.value.code == "LLM_TRANSPORT_ERROR"
    assert "TimeoutError" in str(error.value)


def test_target_is_the_openai_compatible_endpoint() -> None:
    transport = FakeTransport((200, completion("x")))
    client(transport).chat("问题", max_tokens=512)
    url, _timeout, auth = transport.calls[0]
    assert url == "https://example.invalid/v1/chat/completions"
    assert auth == "Bearer sk-secret-do-not-log"
    assert transport.bodies[0]["model"] == "test-model"


# --------------------------------------------------------------------- 解析与校验


def test_parse_json_robust_handles_the_shapes_we_actually_get() -> None:
    payload = '{"triggers":[{"concept":"端侧AI部署","confidence":0.86}]}'
    assert parse_json_robust(payload)["triggers"][0]["concept"] == "端侧AI部署"
    assert parse_json_robust(f"```json\n{payload}\n```")["triggers"][0]["concept"] == "端侧AI部署"
    # 实测：模型答完 JSON 还会补一段说明文字
    assert parse_json_robust(f"{payload}\n\n以上就是三个触发器。")["triggers"][0]["concept"] == "端侧AI部署"
    assert parse_json_robust("完全不是 JSON") is None
    assert parse_json_robust("") is None


def test_validate_triggers_clamps_and_drops() -> None:
    data = {
        "triggers": [
            {"concept": "端侧AI部署", "bridge": "线索→推断", "confidence": 0.86,
             "activation_patterns": ["问题1", "问题2", "问题3", "问题4"]},
            {"concept": "  端侧AI部署  ", "confidence": 0.5},          # 归一化后重复 → 丢弃
            {"concept": "", "confidence": 0.9},                        # 概念为空 → 丢弃
            {"concept": "模型推理优化", "confidence": 5, "activation_patterns": "不是数组"},
            {"concept": "x" * 200, "confidence": -3},
            "不是对象",
        ]
    }
    out = validate_triggers(data)
    assert [entry["concept"] for entry in out] == ["端侧AI部署", "模型推理优化", "x" * 60]
    assert len(out[0]["activationPatterns"]) == 3, "句式最多 3 条"
    assert out[1]["confidence"] == 1.0 and out[1]["activationPatterns"] == []
    assert out[2]["confidence"] == 0.0
    assert validate_triggers({"triggers": []}) == []
    assert validate_triggers(None) == []
    assert validate_triggers({"triggers": [{"concept": "只有概念"}]})[0]["bridge"] == ""


def test_llm_trigger_generation_surfaces_unusable_output() -> None:
    transport = FakeTransport((200, completion("我看不出有什么触发器。")))
    with pytest.raises(LlmError) as error:
        generate_triggers_with_llm(client(transport), "任意记忆内容")
    assert error.value.code == "LLM_TRIGGERS_UNUSABLE"
    assert "replyHead" in error.value.detail


def test_prompt_is_truncated_and_keeps_quality_rules() -> None:
    prompt = build_prompt("记忆内容" * 400, count=3)
    assert "禁止：复述原句" in prompt and "bridge" in prompt and "activation_patterns" in prompt
    assert prompt.count("记忆内容") < 200, "超长记忆只取前 500 字，避免诱导模型复述原句"


# --------------------------------------------------------------------- 触发器落库


GOOD_REPLY = json.dumps(
    {
        "triggers": [
            {"concept": "端侧AI部署", "bridge": "轻量推理→端侧落地", "confidence": 0.86,
             "activation_patterns": ["端侧部署用哪些推理引擎", "移动端怎么集成推理引擎", "离线设备如何部署模型"]},
            {"concept": "模型推理优化", "bridge": "引擎集成→性能优化", "confidence": 0.72,
             "activation_patterns": ["怎么降低推理延迟", "推理加速怎么做", "部署后如何调优"]},
        ]
    },
    ensure_ascii=False,
)


def _fake_graph_vocabulary(text: str):
    """模拟 `GraphStore.terms_in`：只认这一个图谱名词。"""
    return ["轻量级推理引擎集成"] if "轻量级推理引擎集成" in text else []


def memory_store(llm=None) -> MemoryStore:
    return MemoryStore(":memory:", llm=llm, vocabulary=_fake_graph_vocabulary)


def triggers_of(store: MemoryStore, user_id: str, memory_id: str):
    return next(item for item in store.list_items(user_id) if item["id"] == memory_id)["triggers"]


def test_llm_triggers_are_stored_with_model_provenance() -> None:
    transport = FakeTransport(
        (200, completion(GOOD_REPLY, reasoning=1794)),
        (200, completion(GOOD_REPLY, reasoning=1200)),
    )
    store = memory_store(client(transport))
    try:
        item = store.create("user_local", "skill", "具备或正在学习：轻量级推理引擎集成")
        # 写入时走规则版（快、确定），所以此刻触发器不是模型生成的
        assert {t["generatedBy"] for t in triggers_of(store, "user_local", item["id"])} == {"rule-based"}

        triggers = store.generate_triggers("user_local", item["id"], generator="llm")
        assert [t["concept"] for t in triggers] == ["端侧AI部署", "模型推理优化"]
        assert {t["generatedBy"] for t in triggers} == {"llm"}
        assert {t["generatedByModel"] for t in triggers} == {"test-model"}
        assert store.last_trigger_note["used"] == "llm" and store.last_trigger_note["error"] is None
        # 重建是替换而不是追加
        assert len(store.generate_triggers("user_local", item["id"], generator="llm")) == 2
        assert len(triggers_of(store, "user_local", item["id"])) == 2
    finally:
        store.close()


def test_llm_failure_degrades_to_rule_based_and_says_so() -> None:
    """模型失败绝不能阻断记忆：退回规则版，但 `generated_by` 要标出来。"""
    transport = FakeTransport((500, "upstream exploded"), (500, "upstream exploded"))
    store = memory_store(client(transport))
    try:
        item = store.create("user_local", "skill", "具备或正在学习：轻量级推理引擎集成")
        triggers = store.generate_triggers("user_local", item["id"], generator="llm")
        assert triggers and {t["generatedBy"] for t in triggers} == {"rule-based-fallback"}
        assert triggers[0]["concept"] == "轻量级推理引擎集成"
        note = store.last_trigger_note
        assert note["requested"] == "llm" and note["used"] == "rule-based-fallback"
        assert note["error"]["code"] == "LLM_HTTP_ERROR"
        # 记忆本身仍然可用：联想召回照样命中
        context = store.build_context("user_local", "轻量级推理引擎集成进展怎么样")
        assert context["recalled"] and context["recalled"][0]["channel"].startswith("trigger:")
    finally:
        store.close()


def test_auto_generator_falls_back_when_unconfigured() -> None:
    store = memory_store(LlmClient(None))
    try:
        item = store.create("user_local", "goal", "想做边缘 AI 方向")
        triggers = store.generate_triggers("user_local", item["id"], generator="auto")
        assert {t["generatedBy"] for t in triggers} == {"rule-based"}
        assert store.last_trigger_note["used"] == "rule-based"
    finally:
        store.close()


def test_unknown_generator_is_rejected() -> None:
    store = memory_store(LlmClient(None))
    try:
        item = store.create("user_local", "goal", "想做边缘 AI 方向")
        with pytest.raises(ValueError):
            store.generate_triggers("user_local", item["id"], generator="magic")
    finally:
        store.close()


# --------------------------------------------------------------------- 迁移


def test_migration_adds_new_column_to_existing_database(tmp_path) -> None:
    """老库（没有 generated_by_model）要能直接打开，不重建、不丢数据。"""
    import sqlite3

    db = tmp_path / "legacy.db"
    connection = sqlite3.connect(db)
    connection.executescript(
        "CREATE TABLE memory_items (memory_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, category TEXT NOT NULL,"
        " content TEXT NOT NULL, status TEXT NOT NULL, importance INTEGER NOT NULL, source_type TEXT NOT NULL,"
        " source_id TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',"
        " query_patterns_json TEXT NOT NULL DEFAULT '[]', triggers_pending INTEGER NOT NULL DEFAULT 0,"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL, confirmed_at TEXT,"
        " UNIQUE (user_id, source_type, source_id));"
        "CREATE TABLE memory_triggers (trigger_id TEXT PRIMARY KEY,"
        " memory_id TEXT NOT NULL REFERENCES memory_items (memory_id) ON DELETE CASCADE,"
        " level INTEGER NOT NULL DEFAULT 1, concept TEXT NOT NULL, bridge TEXT NOT NULL DEFAULT '',"
        " activation_patterns_json TEXT NOT NULL DEFAULT '[]', confidence REAL NOT NULL,"
        " generated_by TEXT NOT NULL DEFAULT 'rule-based', created_at TEXT NOT NULL);"
    )
    connection.execute(
        "INSERT INTO memory_items (memory_id, user_id, category, content, status, importance, source_type,"
        " source_id, created_at, updated_at, confirmed_at) VALUES ('memory_old','user_local','goal','老数据',"
        "'confirmed',50,'manual','src-1','2026-09-01T00:00:00Z','2026-09-01T00:00:00Z','2026-09-01T00:00:00Z')"
    )
    connection.commit()
    connection.close()

    store = MemoryStore(str(db), llm=LlmClient(None))
    try:
        assert [item["content"] for item in store.list_items("user_local")] == ["老数据"]
        triggers = store.generate_triggers("user_local", "memory_old", generator="rule-based")
        assert triggers and triggers[0]["generatedByModel"] == ""
    finally:
        store.close()
