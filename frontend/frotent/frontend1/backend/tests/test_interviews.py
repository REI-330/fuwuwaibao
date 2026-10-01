"""模拟面试（`/api/v1/interviews*`）的测试（**不联网**）。

跑法：``python -m pytest backend/tests -q``。

钉住的是这套移植里最容易被做坏的六件事：

* **``requestId`` 幂等**：同一用户同一 requestId 重复创建返回同一条会话，不产生重复行；
* **所有权隔离**：别人的 sessionId 取不到（404），不是「取到了别人的答案」；
* **交卷后不可改**：COMPLETED/EVALUATED 之后再提交答案一律 409；
* **规则兜底一定可用**：端点没配时题目与评分照常返回，且 ``questionSource`` 如实写 ``fallback``；
* **「不知道」不给分**：无效回答在任何口径下都是 0 分（不许拿字数换分）；
* **模型路径真走通**：注入假传输时 ``questionSource='llm'`` 且题目来自模型返回，不是兜底。
"""

from __future__ import annotations

import json
from typing import List

import pytest

from backend.interviews import InterviewStore
from backend.llm import LlmClient, LlmConfig
from backend.cross_role import CrossRoleStore
from backend.memories import MemoryStore
from backend.resume_store import ResumeStore
from backend.server import CareerApi, ProfileStore
from backend.knowledge import GraphStore

RESUME_TEXT = """张小明
杭州 | 13800000000 | xm@example.com
教育背景
2022.09-2026.06  浙江大学  自动化 专业  大三
求职意向：边缘 AI 工程师
专业技能
Python（熟练）、模型量化与部署、轻量级推理引擎集成、ROS
项目经历
项目名称：模型量化部署实践
把 YOLOv5 量化到 INT8 并在 RK3588 上跑通，帧率提升 40%。
"""


class FakeTransport:
    """假传输：把预先排好的 ``(status, body)`` 依次返回，绝不联网。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.bodies = []

    def __call__(self, url, body, headers, timeout):
        self.bodies.append(json.loads(body.decode("utf-8")))
        if not self.responses:
            raise AssertionError("FakeTransport 收到的调用次数超出预期")
        return self.responses.pop(0)

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


def build_api(store, memories, llm=None) -> CareerApi:
    return CareerApi(
        store=store,
        profiles=ProfileStore(store),
        memories=memories,
        interviews=InterviewStore(":memory:", llm=llm or LlmClient()),
        cross_role=CrossRoleStore(":memory:"),
        resumes=ResumeStore(":memory:"),
    )


@pytest.fixture()
def api(store: GraphStore, memories: MemoryStore) -> CareerApi:
    return build_api(store, memories)


def call(api: CareerApi, method: str, path: str, body=None, cookies=None):
    return api.handle(method, path, {}, body, cookies=cookies)


def occupation_role_id(api: CareerApi) -> str:
    _, payload = call(api, "GET", "/api/v1/interview-skills")
    return next(item["roleId"] for item in payload["data"]["items"] if item["roleId"] != "custom")


# ---------------------------------------------------------------------------- 岗位与出题


def test_interview_skills_lists_catalog_plus_custom(api: CareerApi) -> None:
    status, payload = call(api, "GET", "/api/v1/interview-skills")
    assert status == 200
    items = payload["data"]["items"]
    assert items[0]["roleId"] == "custom"
    assert items[0]["name"] == "自定义岗位"
    # 其余来自职业目录（本项目现役 4 个职业），每个都带岗位名与核心技能
    others = items[1:]
    assert others and all(item["roleId"] != "custom" for item in others)
    assert all(isinstance(item["coreSkills"], list) for item in others)


def test_create_interview_uses_fallback_when_endpoint_missing(api: CareerApi) -> None:
    role_id = occupation_role_id(api)
    status, payload = call(api, "POST", "/api/v1/interviews",
                           {"roleId": role_id, "difficulty": "mid", "questionCount": 4})
    assert status == 201
    session = payload["data"]["session"]
    assert session["questionSource"] == "fallback"
    assert session["questionMode"] == "general"
    assert session["status"] == "IN_PROGRESS"
    assert session["totalQuestions"] == 4
    assert len(session["questions"]) == 4
    assert [q["position"] for q in session["questions"]] == [0, 1, 2, 3]
    assert all(q["question"] and q["category"] for q in session["questions"])


def test_create_interview_is_idempotent_by_request_id(api: CareerApi) -> None:
    role_id = occupation_role_id(api)
    body = {"roleId": role_id, "difficulty": "mid", "questionCount": 3, "requestId": "same-request"}
    first = call(api, "POST", "/api/v1/interviews", body)[1]["data"]["session"]
    second = call(api, "POST", "/api/v1/interviews", body)[1]["data"]["session"]
    assert first["sessionId"] == second["sessionId"]
    _, listing = call(api, "GET", "/api/v1/interviews")
    assert len(listing["data"]["items"]) == 1


def test_create_interview_rejects_bad_input(api: CareerApi) -> None:
    assert call(api, "POST", "/api/v1/interviews", {"roleId": "NOPE"})[1]["error"]["code"] == "ROLE_NOT_FOUND"
    assert call(api, "POST", "/api/v1/interviews",
                {"roleId": "custom", "questionCount": 3})[1]["error"]["code"] == "ROLE_NAME_REQUIRED"
    role_id = occupation_role_id(api)
    status, payload = call(api, "POST", "/api/v1/interviews",
                           {"roleId": role_id, "difficulty": "impossible", "questionCount": 3})
    assert status == 422 and payload["error"]["code"] == "INVALID_DIFFICULTY"
    assert call(api, "POST", "/api/v1/interviews",
                {"roleId": role_id, "questionCount": 99})[1]["error"]["code"] == "INVALID_BODY"
    # 自定义岗位：有名字就能建
    status, payload = call(api, "POST", "/api/v1/interviews",
                           {"roleId": "custom", "roleName": "AI 产品经理", "questionCount": 3})
    assert status == 201
    assert payload["data"]["session"]["roleName"] == "AI 产品经理"


def test_model_path_is_used_when_endpoint_configured(store: GraphStore, memories: MemoryStore) -> None:
    questions = [{"question": f"模型题 {i}", "category": "模型维度", "referenceAnswer": "参考", "keyPoints": ["要点"]}
                 for i in range(3)]
    transport = FakeTransport((200, completion(json.dumps({"questions": questions}, ensure_ascii=False))))
    api = build_api(store, memories, llm=make_client(transport))
    role_id = occupation_role_id(api)
    status, payload = call(api, "POST", "/api/v1/interviews",
                           {"roleId": role_id, "difficulty": "mid", "questionCount": 3})
    assert status == 201
    session = payload["data"]["session"]
    assert session["questionSource"] == "llm"
    assert [q["question"] for q in session["questions"]] == ["模型题 0", "模型题 1", "模型题 2"]
    # prompt 里必须带上「简历是不可信数据」这条防注入要求
    sent = transport.bodies[0]["messages"][0]["content"]
    assert "不可信数据" in sent and "questionCount" in sent


def test_model_returns_garbage_falls_back(store: GraphStore, memories: MemoryStore) -> None:
    transport = FakeTransport((200, completion("这不是 JSON")))
    api = build_api(store, memories, llm=make_client(transport))
    role_id = occupation_role_id(api)
    status, payload = call(api, "POST", "/api/v1/interviews",
                           {"roleId": role_id, "questionCount": 3})
    assert status == 201
    assert payload["data"]["session"]["questionSource"] == "fallback"


# ---------------------------------------------------------------------------- 作答与评分


def test_answer_flow_and_scoring(api: CareerApi) -> None:
    role_id = occupation_role_id(api)
    session = call(api, "POST", "/api/v1/interviews",
                   {"roleId": role_id, "questionCount": 3})[1]["data"]["session"]
    session_id = session["sessionId"]
    first, second, third = session["questions"]

    # 空答案不接受
    assert call(api, "POST", f"/api/v1/interviews/{session_id}/answers",
                {"questionId": first["questionId"], "answer": "   "})[1]["error"]["code"] == "INVALID_BODY"
    # 别人的题目
    assert call(api, "POST", f"/api/v1/interviews/{session_id}/answers",
                {"questionId": "question_not_mine", "answer": "x"})[1]["error"]["code"] == "QUESTION_NOT_FOUND"

    status, payload = call(api, "POST", f"/api/v1/interviews/{session_id}/answers",
                           {"questionId": first["questionId"],
                            "answer": "首先说明背景，其次给出判断和行动，例如把帧率从 12 提到 17，最后复盘风险。"})
    assert status == 200
    assert payload["data"]["session"]["currentQuestionIndex"] == 1
    # 无效回答
    call(api, "POST", f"/api/v1/interviews/{session_id}/answers",
         {"questionId": second["questionId"], "answer": "不知道"})
    call(api, "POST", f"/api/v1/interviews/{session_id}/answers",
         {"questionId": third["questionId"], "answer": "不会"})

    status, payload = call(api, "POST", f"/api/v1/interviews/{session_id}/complete")
    assert status == 200
    report = payload["data"]["report"]
    scores = {item["questionId"]: item["score"] for item in report["questionDetails"]}
    assert scores[first["questionId"]] > 0
    assert scores[second["questionId"]] == 0
    assert scores[third["questionId"]] == 0
    assert report["answeredQuestions"] == 3
    assert report["categoryScores"] and sum(item["questionCount"] for item in report["categoryScores"]) == 3
    assert report["strengths"] and report["improvements"]

    # 交卷后再改答案：409
    status, payload = call(api, "POST", f"/api/v1/interviews/{session_id}/answers",
                           {"questionId": first["questionId"], "answer": "改一下"})
    assert status == 409 and payload["error"]["code"] == "INTERVIEW_FINISHED"
    # 报告可重复读取，且与交卷时一致
    again = call(api, "GET", f"/api/v1/interviews/{session_id}/report")[1]["data"]["report"]
    assert again["overallScore"] == report["overallScore"]
    # 重复交卷不会覆盖报告
    duplicate = call(api, "POST", f"/api/v1/interviews/{session_id}/complete")[1]["data"]["report"]
    assert duplicate["overallScore"] == report["overallScore"]


def test_report_not_ready_before_complete(api: CareerApi) -> None:
    role_id = occupation_role_id(api)
    session_id = call(api, "POST", "/api/v1/interviews",
                      {"roleId": role_id, "questionCount": 3})[1]["data"]["session"]["sessionId"]
    status, payload = call(api, "GET", f"/api/v1/interviews/{session_id}/report")
    assert status == 409 and payload["error"]["code"] == "REPORT_NOT_READY"


class PromptAwareTransport:
    """按 prompt 的 ``task`` 分派，并且**回显本题集的 questionId**。

    这样才真的验证到「模型返回要按本题集的 id 对齐」——如果拿一组编造的 id，
    就变成在测「错了会不会崩」，而不是测正确路径。
    """

    def __init__(self) -> None:
        self.bodies: List[dict] = []

    def __call__(self, url, body, headers, timeout):
        payload = json.loads(body.decode("utf-8"))
        self.bodies.append(payload)
        prompt = json.loads(payload["messages"][0]["content"])
        if prompt["task"].startswith("生成结构化"):
            content = json.dumps({
                "questions": [
                    {"question": f"模型题 {i}", "category": "模型维度", "referenceAnswer": "参考", "keyPoints": ["要点"]}
                    for i in range(prompt["questionCount"])
                ]
            }, ensure_ascii=False)
        else:
            content = json.dumps({
                "overallScore": 88,
                "overallFeedback": "模型总结",
                "strengths": ["模型亮点"],
                "improvements": ["模型建议"],
                "questionDetails": [
                    {"questionId": item["questionId"], "score": 88, "feedback": "模型反馈"}
                    for item in prompt["qa"]
                ],
            }, ensure_ascii=False)
        return 200, json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}, ensure_ascii=False)

    @property
    def calls(self) -> int:
        return len(self.bodies)


def test_model_evaluation_path(store: GraphStore, memories: MemoryStore) -> None:
    transport = PromptAwareTransport()
    api = build_api(store, memories, llm=LlmClient(
        LlmConfig(base_url="http://llm.invalid", api_key="test-key", model="test-model", source="test"),
        transport=transport, sleep=lambda _seconds: None))
    role_id = occupation_role_id(api)
    session = call(api, "POST", "/api/v1/interviews",
                   {"roleId": role_id, "questionCount": 3})[1]["data"]["session"]
    assert session["questionSource"] == "llm"
    session_id = session["sessionId"]
    for question in session["questions"]:
        call(api, "POST", f"/api/v1/interviews/{session_id}/answers",
             {"questionId": question["questionId"], "answer": "有结构的具体回答，例如项目里的实测数据。"})
    report = call(api, "POST", f"/api/v1/interviews/{session_id}/complete")[1]["data"]["report"]
    assert report["overallFeedback"] == "模型总结"
    assert all(item["score"] == 88 and item["feedback"] == "模型反馈" for item in report["questionDetails"])
    assert transport.calls == 2


def test_model_evaluation_with_unknown_ids_does_not_cross_contaminate(store: GraphStore, memories: MemoryStore) -> None:
    """模型回了编造的 questionId：那些分数不许串到本题的题目上，兜底反馈原样保留。"""
    questions = [{"question": f"题 {i}", "category": "维度", "referenceAnswer": "参考", "keyPoints": ["要点"]}
                 for i in range(3)]
    transport = FakeTransport(
        (200, completion(json.dumps({"questions": questions}, ensure_ascii=False))),
        (200, completion(json.dumps({
            "overallScore": 88, "overallFeedback": "模型总结",
            "strengths": ["模型亮点"], "improvements": ["模型建议"],
            "questionDetails": [{"questionId": f"x{i}", "score": 88, "feedback": "模型反馈"} for i in range(3)],
        }, ensure_ascii=False))),
    )
    api = build_api(store, memories, llm=make_client(transport))
    role_id = occupation_role_id(api)
    session = call(api, "POST", "/api/v1/interviews",
                   {"roleId": role_id, "questionCount": 3})[1]["data"]["session"]
    session_id = session["sessionId"]
    for question in session["questions"]:
        call(api, "POST", f"/api/v1/interviews/{session_id}/answers",
             {"questionId": question["questionId"], "answer": "有结构的具体回答，例如项目里的实测数据。"})
    report = call(api, "POST", f"/api/v1/interviews/{session_id}/complete")[1]["data"]["report"]
    assert report["overallFeedback"] == "模型总结"
    # 逐题反馈仍是兜底算出来的那份，没有被编造的 id 串走
    assert all(item["feedback"] != "模型反馈" for item in report["questionDetails"])
    assert all(item["score"] != 88 for item in report["questionDetails"])


# ---------------------------------------------------------------------------- 归属、删除、简历面试


def test_sessions_are_isolated_by_cookie(api: CareerApi) -> None:
    role_id = occupation_role_id(api)
    owner = {"career_session": "user_000000000001"}
    other = {"career_session": "user_000000000002"}
    session_id = call(api, "POST", "/api/v1/interviews",
                      {"roleId": role_id, "questionCount": 3}, cookies=owner)[1]["data"]["session"]["sessionId"]
    assert call(api, "GET", f"/api/v1/interviews/{session_id}", cookies=owner)[0] == 200
    status, payload = call(api, "GET", f"/api/v1/interviews/{session_id}", cookies=other)
    assert status == 404 and payload["error"]["code"] == "INTERVIEW_NOT_FOUND"
    assert call(api, "GET", "/api/v1/interviews", cookies=other)[1]["data"]["items"] == []
    assert len(call(api, "GET", "/api/v1/interviews", cookies=owner)[1]["data"]["items"]) == 1


def test_delete_interview(api: CareerApi) -> None:
    role_id = occupation_role_id(api)
    session_id = call(api, "POST", "/api/v1/interviews",
                      {"roleId": role_id, "questionCount": 3})[1]["data"]["session"]["sessionId"]
    assert call(api, "DELETE", f"/api/v1/interviews/{session_id}")[1]["data"] == {"deleted": True}
    assert call(api, "GET", f"/api/v1/interviews/{session_id}")[0] == 404
    assert call(api, "DELETE", f"/api/v1/interviews/{session_id}")[0] == 404


def test_resume_based_interview(api: CareerApi) -> None:
    status, payload = call(api, "POST", "/api/resumes/extract", {"text": RESUME_TEXT})
    assert status == 200
    resume_id = payload["data"]["resumeId"]
    _, listing = call(api, "GET", "/api/resumes")
    assert [item["resumeId"] for item in listing["data"]["items"]] == [resume_id]
    assert listing["data"]["items"][0]["filename"] == "pasted-resume.txt"

    role_id = occupation_role_id(api)
    status, payload = call(api, "POST", "/api/v1/interviews",
                           {"roleId": role_id, "questionCount": 5, "resumeId": resume_id})
    assert status == 201
    session = payload["data"]["session"]
    assert session["questionMode"] == "resume"
    assert session["resumeId"] == resume_id
    assert session["resumeFilename"] == "pasted-resume.txt"
    # 约 60% 的题来自简历（5 题 → 3 题）
    resume_questions = [q for q in session["questions"] if "简历" in q["question"]]
    assert len(resume_questions) == 3
    # 未知简历
    assert call(api, "POST", "/api/v1/interviews",
                {"roleId": role_id, "questionCount": 3, "resumeId": "resume_nope"})[1]["error"]["code"] == "RESUME_NOT_FOUND"
    # 摘要接口
    assert call(api, "GET", f"/api/resumes/{resume_id}")[1]["data"]["resume"]["resumeId"] == resume_id
    assert call(api, "GET", "/api/resumes/resume_missing")[0] == 404
