"""对话（`POST /api/chat`）与记忆注入的测试（**不联网**）。

跑法：``python -m pytest backend/tests -q``。

钉住的是「注入这件事」而不是文风，所以断言都落在可复核的地方：
* 无记忆时**不改写提问**、提示词里**没有**记忆块（不是空标题占位）；
* 有记忆时提示词里真的带上已确认内容，且 `injected.memoryIds` 指得回去；
* 模型没配 / 调用失败都不许变成 5xx：降级为规则版并带错误码与原因；
* 规则版答案必须有图谱依据（技能缺口取自 `requires` 边），不是套话。
"""

from __future__ import annotations

import json

import pytest

from backend.chat import (
    GRAPH_TITLE,
    HISTORY_TITLE,
    HISTORY_TURNS,
    MEMORY_TITLE,
    PROMPT_TEMPLATE,
    RULE_MARKER,
    ChatService,
    compose_prompt,
)
from backend.knowledge import GraphStore
from backend.llm import LlmClient, LlmConfig
from backend.memories import MemoryStore
from backend.server import CareerApi, ProfileStore


class FakeTransport:
    """按顺序回放预设响应，并记录每次请求体（用于复核「到底喂了什么给模型」）。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.bodies = []

    def __call__(self, url, body, headers, timeout):
        self.bodies.append(json.loads(body.decode("utf-8")))
        if not self.responses:
            raise AssertionError("FakeTransport 收到的调用次数超出预期")
        return self.responses.pop(0)

    @property
    def prompt(self) -> str:
        return self.bodies[-1]["messages"][0]["content"]

    @property
    def calls(self) -> int:
        return len(self.bodies)


def completion(content: str) -> str:
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}, ensure_ascii=False)


def make_client(transport: FakeTransport) -> LlmClient:
    return LlmClient(
        LlmConfig(base_url="http://llm.invalid", api_key="test-key", model="test-model", source="test"),
        transport=transport,
        sleep=lambda _seconds: None,
    )


@pytest.fixture(scope="module")
def store() -> GraphStore:
    return GraphStore()


@pytest.fixture()
def memories(store: GraphStore) -> MemoryStore:
    instance = MemoryStore(":memory:", vocabulary=store.terms_in)
    yield instance
    instance.close()


@pytest.fixture()
def profiles(store: GraphStore) -> ProfileStore:
    return ProfileStore(store)


def build_api(store: GraphStore, memories: MemoryStore, profiles: ProfileStore, transport=None) -> CareerApi:
    client = make_client(transport) if transport is not None else None
    chats = ChatService(store, memories, profiles, client=client)
    return CareerApi(store=store, profiles=profiles, memories=memories, chats=chats)


def confirm_profile(api: CareerApi, payload: dict) -> None:
    api.handle("PUT", "/api/profile", {}, payload)
    api.handle("POST", "/api/profile/confirm", {}, None)


def confirm_memory(api: CareerApi, category: str, content: str) -> str:
    status, payload = api.handle("POST", "/api/memories", {}, {"category": category, "content": content})
    assert status == 201
    return payload["data"]["item"]["id"]


# --------------------------------------------------------------------- 提示词组装纪律

def test_compose_prompt_omits_absent_blocks() -> None:
    """空块整块缺席：不插空标题、不留占位符（否则查询会被静默污染）。"""
    prompt = compose_prompt(memory_text="", graph_text="", message="你好")
    # 系统前缀的说明文字里会出现这些名字，所以只断言**块标题**没有被空块带出来
    assert MEMORY_TITLE not in prompt
    assert GRAPH_TITLE not in prompt
    assert prompt.endswith("【用户这次说】\n你好")


def test_prompt_without_memory_is_prefix_plus_message() -> None:
    """无记忆 + 无图谱事实时，提示词就是「固定系统前缀 + 用户原文」，逐字节确定。"""
    from backend.chat import SYSTEM_PREFIX

    first = compose_prompt(memory_text="", graph_text="", message="我想做边缘 AI 工程师")
    second = compose_prompt(memory_text="", graph_text="", message="我想做边缘 AI 工程师")
    assert first == second
    assert first == f"{SYSTEM_PREFIX}\n【用户这次说】\n我想做边缘 AI 工程师"


# --------------------------------------------------------------------- 无模型：规则版也要能用

def test_chat_without_memory_does_not_rewrite_query(store, memories, profiles) -> None:
    api = build_api(store, memories, profiles)
    message = "  边缘 AI 工程师需要什么技能？  "
    status, payload = api.handle("POST", "/api/chat", {}, {"message": message})

    assert status == 200
    data = payload["data"]
    # 检索用的就是用户原话（只去掉首尾空白），没有被扩写或拼接
    assert data["injected"]["query"] == message.strip()
    assert data["injected"]["count"] == 0
    assert data["injected"]["memoryHash"] == ""
    assert data["injected"]["promptTemplate"] == PROMPT_TEMPLATE
    assert data["provider"] == "rule-based"
    assert data["message"].startswith(RULE_MARKER)
    assert data["conversationId"].startswith("conversation_")
    # 没有任何记忆块（不是空标题占位）
    assert data["injected"]["summaryText"] == ""


def test_rule_reply_grounds_in_graph(store, memories, profiles) -> None:
    """规则版答案必须有图谱依据：「还差…」里的技能名来自 requires 边。"""
    api = build_api(store, memories, profiles)
    _, payload = api.handle("POST", "/api/chat", {}, {"message": "边缘 AI 工程师需要什么技能？"})
    message = payload["data"]["message"]

    assert "边缘 AI 工程师" in message
    assert "还差：" in message
    assert "模型量化与部署" in message  # AI004 要求技能里权重最高的一项
    # 缺口结论要能回到导出文件核验，而不是模型编的
    required = {row["skillId"] for row in store.required_skills("occupation:AI004")}
    assert required, "AI004 应当有 requires 边"


def test_rule_reply_asks_for_background_when_profile_empty(store, memories, profiles) -> None:
    api = build_api(store, memories, profiles)
    _, payload = api.handle("POST", "/api/chat", {}, {"message": "你好"})
    assert "告诉我" in payload["data"]["message"]


# --------------------------------------------------------------------- 有模型：注入 + 直出

def test_chat_injects_confirmed_memory_into_prompt(store, memories, profiles) -> None:
    transport = FakeTransport((200, completion("记得你在学模型量化，先把它落到作品里。")))
    api = build_api(store, memories, profiles, transport)
    memory_id = confirm_memory(api, "skill", "具备或正在学习：模型量化与部署")

    _, payload = api.handle("POST", "/api/chat", {}, {"message": "模型量化那块我该怎么做？"})
    data = payload["data"]

    assert transport.calls == 1
    assert MEMORY_TITLE in transport.prompt
    summary = data["injected"]["summaryText"]
    assert summary and summary in transport.prompt
    assert "【用户这次说】\n模型量化那块我该怎么做？" in transport.prompt
    assert data["provider"] == "llm"
    assert data["llm"]["used"] == "llm"
    assert data["llm"]["model"] == "test-model"
    assert data["llm"]["error"] is None
    assert memory_id in data["injected"]["memoryIds"]
    assert data["injected"]["count"] >= 1
    assert data["message"] == "记得你在学模型量化，先把它落到作品里。"


def test_candidate_memory_never_injected(store, memories, profiles) -> None:
    """授权边界在对话侧同样成立：候选不进提示词（图谱事实块里出现同名技能是另一回事）。"""
    transport = FakeTransport((200, completion("好的。")))
    api = build_api(store, memories, profiles, transport)
    api.handle("POST", "/api/memories", {}, {"category": "skill", "content": "具备或正在学习：模型量化与部署",
                                            "status": "candidate"})

    _, payload = api.handle("POST", "/api/chat", {}, {"message": "模型量化那块我该怎么做？"})
    assert MEMORY_TITLE not in transport.prompt
    assert payload["data"]["injected"]["count"] == 0
    assert payload["data"]["injected"]["memoryIds"] == []


def test_generator_rule_based_never_calls_model(store, memories, profiles) -> None:
    transport = FakeTransport()  # 一旦被调用就会断言失败
    api = build_api(store, memories, profiles, transport)
    status, payload = api.handle("POST", "/api/chat", {"generator": ["rule-based"]}, {"message": "边缘 AI 工程师需要什么"})

    assert status == 200
    assert transport.calls == 0
    assert payload["data"]["provider"] == "rule-based"
    assert payload["data"]["llm"]["requested"] == "rule-based"


def test_generator_llm_forced_without_endpoint_degrades(store, memories, profiles) -> None:
    """显式要模型但端点没配 → 仍然 200，降级并说明原因（不许假装有模型）。"""
    api = build_api(store, memories, profiles)  # client=None → 复用未配置的记忆库客户端
    status, payload = api.handle("POST", "/api/chat", {"generator": ["llm"]}, {"message": "你好"})

    assert status == 200
    assert payload["data"]["provider"] == "rule-based"
    assert payload["data"]["message"].startswith(RULE_MARKER)


# --------------------------------------------------------------------- 失败降级

@pytest.mark.parametrize(
    "response",
    [
        (500, "boom"),
        (200, json.dumps({"choices": [{"message": {"content": ""}}],
                          "usage": {"completion_tokens": 2048,
                                    "completion_tokens_details": {"reasoning_tokens": 2048}}})),
    ],
)
def test_chat_degrades_to_rule_based_on_model_failure(store, memories, profiles, response) -> None:
    transport = FakeTransport(response, response, response, response, response, response)
    api = build_api(store, memories, profiles, transport)
    status, payload = api.handle("POST", "/api/chat", {}, {"message": "边缘 AI 工程师需要什么技能？"})
    data = payload["data"]

    # 模型挂了不是 5xx：用户仍然拿得到一条有依据的规则版回答
    assert status == 200
    assert data["provider"] == "rule-based"
    assert data["llm"]["used"] == "rule-based"
    assert data["llm"]["error"]["code"] in ("LLM_HTTP_ERROR", "LLM_EMPTY_CONTENT")
    assert data["llm"]["error"]["message"]
    assert data["message"].startswith(RULE_MARKER)
    assert "边缘 AI 工程师" in data["message"]


# --------------------------------------------------------------------- 会话表

def test_conversation_id_reused_and_history_capped(store, memories, profiles) -> None:
    transport = FakeTransport(*[(200, completion(f"第 {index} 轮回答")) for index in range(HISTORY_TURNS + 2)])
    api = build_api(store, memories, profiles, transport)

    _, first = api.handle("POST", "/api/chat", {}, {"message": "第一句"})
    conversation_id = first["data"]["conversationId"]
    for index in range(1, HISTORY_TURNS + 1):
        _, payload = api.handle("POST", "/api/chat", {}, {"message": f"第 {index} 句",
                                                          "conversationId": conversation_id})
        assert payload["data"]["conversationId"] == conversation_id

    # 最后一轮的提示词里带着前几轮对话（多轮上下文确实进了模型）
    assert "【前几轮对话】" in transport.prompt
    assert "第一句" in transport.prompt
    # 历史有上限，不会无限增长
    assert len(api.chats.history(conversation_id)) == HISTORY_TURNS * 2


def test_chat_rejects_bad_requests(store, memories, profiles) -> None:
    api = build_api(store, memories, profiles)
    empty = api.handle("POST", "/api/chat", {}, {"message": "   "})
    assert empty[0] == 400 and empty[1]["error"]["code"] == "INVALID_CHAT_MESSAGE"

    missing = api.handle("POST", "/api/chat", {}, None)
    assert missing[0] == 400 and missing[1]["error"]["code"] == "INVALID_BODY"

    bad_generator = api.handle("POST", "/api/chat", {"generator": ["magic"]}, {"message": "你好"})
    assert bad_generator[0] == 400 and bad_generator[1]["error"]["code"] == "INVALID_GENERATOR"


def test_memory_augmented_profile_reaches_chat(store, memories, profiles) -> None:
    """已确认记忆补画像空缺：对话里的「已覆盖 N 项」应当随之变化。"""
    api = build_api(store, memories, profiles)
    confirm_profile(api, {"identity": "学生"})
    _, before = api.handle("POST", "/api/chat", {}, {"message": "边缘 AI 工程师需要什么技能？"})
    assert "你已经覆盖 0 项" in before["data"]["message"]

    confirm_memory(api, "skill", "具备或正在学习：模型量化与部署")
    _, after = api.handle("POST", "/api/chat", {}, {"message": "边缘 AI 工程师需要什么技能？"})
    assert "你已经覆盖 1 项" in after["data"]["message"]


# --------------------------------------------------------------------- 会话落库（刷新 / 重启不丢）

def test_chat_sessions_and_messages_persist(store, memories, profiles, tmp_path) -> None:
    """会话与消息落库：换一个 ChatService 实例（= 后端重启）之后，同一段对话还读得回来，
    而且重启后的那一轮**真的把历史拼进了提示词**。"""
    from backend.chat import ChatStore

    db = str(tmp_path / "chat.db")
    api = CareerApi(store=store, profiles=profiles, memories=memories,
                    chats=ChatService(store, memories, profiles, sessions=ChatStore(db)))

    status, payload = api.handle("POST", "/api/chat", {}, {"message": "边缘 AI 工程师需要什么技能？"})
    assert status == 200
    session_id = payload["data"]["conversationId"]

    _, sessions = api.handle("GET", "/api/chat/sessions", {}, None)
    assert sessions["data"]["persisted"] is True and sessions["data"]["count"] == 1
    row = sessions["data"]["items"][0]
    assert row["sessionId"] == session_id and row["messageCount"] == 2
    assert row["title"].startswith("边缘 AI 工程师"), "标题取第一句用户消息"

    _, detail = api.handle("GET", f"/api/chat/sessions/{session_id}", {}, None)
    messages = detail["data"]["messages"]
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert messages[0]["text"] == "边缘 AI 工程师需要什么技能？"
    assert messages[1]["provider"] == "rule-based", "走的是哪条路要如实落库"

    _, second = api.handle("POST", "/api/chat", {}, {"message": "那我现在该先做什么？", "conversationId": session_id})
    assert second["data"]["conversationId"] == session_id, "带 conversationId 要追加到同一段会话"
    _, after = api.handle("GET", f"/api/chat/sessions/{session_id}", {}, None)
    assert len(after["data"]["messages"]) == 4

    # 「重启」：新实例 + 新连接读同一份库
    transport = FakeTransport((200, completion("先把模型量化落到一个能跑的作品里。")))
    restarted = CareerApi(store=store, profiles=profiles, memories=memories,
                          chats=ChatService(store, memories, profiles, client=make_client(transport),
                                            sessions=ChatStore(db)))
    _, resumed = restarted.handle("POST", "/api/chat", {}, {"message": "继续", "conversationId": session_id})
    assert resumed["data"]["conversationId"] == session_id
    assert HISTORY_TITLE in transport.prompt, "重启后这一轮必须把库里的历史拼进提示词"
    assert "边缘 AI 工程师需要什么技能？" in transport.prompt
    assert restarted.handle("GET", f"/api/chat/sessions/{session_id}", {}, None)[1]["data"]["messages"][-1]["role"] == "assistant"

    # 归属隔离：别人的会话对我就是「不存在」
    other = {"career_session": "user_0000000000d1"}
    assert restarted.handle("GET", "/api/chat/sessions", {}, None, cookies=other)[1]["data"]["count"] == 0
    assert restarted.handle("GET", f"/api/chat/sessions/{session_id}", {}, None, cookies=other)[0] == 404
    assert restarted.handle("DELETE", f"/api/chat/sessions/{session_id}", {}, None, cookies=other)[0] == 404
    assert restarted.handle("GET", "/api/chat/sessions/nope", {}, None)[0] == 404

    # 删除：会话与消息一起清掉
    status, removed = restarted.handle("DELETE", f"/api/chat/sessions/{session_id}", {}, None)
    assert status == 200 and removed["data"]["removedMessages"] == 6
    assert restarted.handle("GET", f"/api/chat/sessions/{session_id}", {}, None)[0] == 404
    assert restarted.handle("GET", "/api/chat/sessions", {}, None)[1]["data"]["count"] == 0


def test_chat_without_session_store_is_honest(store, memories, profiles) -> None:
    """没挂会话库时**如实说没挂**，而不是假装「没有历史」。"""
    api = build_api(store, memories, profiles)
    status, payload = api.handle("GET", "/api/chat/sessions", {}, None)
    assert status == 200
    assert payload["data"]["persisted"] is False and "没有挂会话库" in payload["data"]["note"]
    assert api.handle("GET", "/api/chat/sessions/conversation_x", {}, None)[0] == 404
