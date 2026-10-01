"""跨岗位沟通训练（`/api/v1/cross-role/*`）的测试（**离线**）。

跑法：``python -m pytest backend/tests -q``。

钉住的是：

* **题库完整**：32 个职业 / 320 题，且带来源与免责声明（这是训练材料的出处）；
* **选项顺序随机**：同一道题在两个会话里选项 id 不同（防止「答案永远是 B」）；
* **仅接受本题选项**：别的题的 optionId 一律 422，不是静默记 0 分；
* **练习/测评模式差异**：练习模式答完立刻揭晓，测评模式交卷后才揭晓；
* **报告口径**：4 个维度、答错按 0.35 计入（不是 0）、带免责声明；
* **幂等与归属**：requestId 幂等、按会话 Cookie 隔离、交卷后不可改（409）。
"""

from __future__ import annotations

import pytest

from backend.cross_role import DIMENSION_NAMES, CrossRoleStore, bank_metadata, get_role, list_roles
from backend.interviews import InterviewStore
from backend.llm import LlmClient
from backend.memories import MemoryStore
from backend.resume_store import ResumeStore
from backend.server import CareerApi, ProfileStore
from backend.knowledge import GraphStore

ROLE_ID = "AI009"


@pytest.fixture(scope="module")
def store() -> GraphStore:
    return GraphStore()


@pytest.fixture()
def memories(store: GraphStore) -> MemoryStore:
    instance = MemoryStore(":memory:", vocabulary=store.terms_in)
    yield instance
    instance.close()


@pytest.fixture()
def api(store: GraphStore, memories: MemoryStore) -> CareerApi:
    return CareerApi(
        store=store,
        profiles=ProfileStore(store),
        memories=memories,
        interviews=InterviewStore(":memory:", llm=LlmClient()),
        cross_role=CrossRoleStore(":memory:"),
        resumes=ResumeStore(":memory:"),
    )


def call(api: CareerApi, method: str, path: str, body=None, cookies=None):
    return api.handle(method, path, {}, body, cookies=cookies)


def start(api: CareerApi, mode: str = "practice", cookies=None) -> dict:
    status, payload = call(api, "POST", "/api/v1/cross-role/sessions",
                           {"roleId": ROLE_ID, "mode": mode}, cookies=cookies)
    assert status == 201, payload
    return payload["data"]["session"]


# ---------------------------------------------------------------------------- 题库


def test_question_bank_is_complete() -> None:
    roles = list_roles()
    assert len(roles) == 32
    assert sum(role["questionCount"] for role in roles) == 320
    assert all(role["category"] for role in roles)
    metadata = bank_metadata()
    assert metadata["roleCount"] == 32 and metadata["questionCount"] == 320
    # 来源与免责声明必须随接口一起给出去，不能只留在文件里
    assert metadata["source"] == "questionnaire-analysis-archive"
    assert "not a formal talent assessment" in metadata["disclaimer"]


def test_roles_endpoint_exposes_source_and_count(api: CareerApi) -> None:
    status, payload = call(api, "GET", "/api/v1/cross-role/roles")
    assert status == 200
    data = payload["data"]
    assert data["count"] == 32
    assert len(data["items"]) == 32
    assert data["disclaimer"]
    assert set(DIMENSION_NAMES) == {"delivery", "trust", "alignment", "riskControl"}


def test_unknown_role_is_404(api: CareerApi) -> None:
    status, payload = call(api, "POST", "/api/v1/cross-role/sessions", {"roleId": "AI999"})
    assert status == 404 and payload["error"]["code"] == "ROLE_NOT_FOUND"
    assert call(api, "POST", "/api/v1/cross-role/sessions", {"roleId": ROLE_ID, "mode": "nonsense"})[1]["error"]["code"] == "INVALID_MODE"


# ---------------------------------------------------------------------------- 会话与选项


def test_create_session_shape_and_option_shuffle(api: CareerApi) -> None:
    role = get_role(ROLE_ID)
    session = start(api)
    assert session["status"] == "IN_PROGRESS"
    assert session["mode"] == "practice"
    assert session["totalQuestions"] == len(role["questions"]) == 10
    first = session["questions"][0]
    assert len(first["options"]) == 4
    assert len({option["optionId"] for option in first["options"]}) == 4
    assert all(option["text"] for option in first["options"])
    assert first["selectedOptionId"] is None and first["feedback"] is None

    # 同一道源题在新会话里选项 id 重新生成 —— 不能靠记序号作弊
    other = start(api)
    assert other["questions"][0]["question"] == first["question"]
    assert other["questions"][0]["options"] != first["options"]


def test_request_id_is_idempotent(api: CareerApi) -> None:
    body = {"roleId": ROLE_ID, "mode": "practice", "requestId": "cr-same"}
    first = call(api, "POST", "/api/v1/cross-role/sessions", body)[1]["data"]["session"]
    second = call(api, "POST", "/api/v1/cross-role/sessions", body)[1]["data"]["session"]
    assert first["sessionId"] == second["sessionId"]
    assert len(call(api, "GET", "/api/v1/cross-role/sessions")[1]["data"]["items"]) == 1


def test_answer_must_be_one_of_this_question_options(api: CareerApi) -> None:
    session = start(api)
    question, other = session["questions"][0], session["questions"][1]
    status, payload = call(api, "POST", f"/api/v1/cross-role/sessions/{session['sessionId']}/answers",
                           {"questionId": question["questionId"], "optionId": "deadbeef"})
    assert status == 422 and payload["error"]["code"] == "OPTION_NOT_FOUND"
    # 别的题目的 optionId 也不行（它确实存在，但不属于这道题）
    status, payload = call(api, "POST", f"/api/v1/cross-role/sessions/{session['sessionId']}/answers",
                           {"questionId": question["questionId"], "optionId": other["options"][0]["optionId"]})
    assert status == 422 and payload["error"]["code"] == "OPTION_NOT_FOUND"
    assert call(api, "POST", f"/api/v1/cross-role/sessions/{session['sessionId']}/answers",
                {"questionId": "nope", "optionId": question["options"][0]["optionId"]})[1]["error"]["code"] == "QUESTION_NOT_FOUND"


def test_practice_mode_reveals_feedback_immediately(api: CareerApi) -> None:
    session = start(api, "practice")
    session_id = session["sessionId"]
    question = session["questions"][0]
    status, payload = call(api, "POST", f"/api/v1/cross-role/sessions/{session_id}/answers",
                           {"questionId": question["questionId"], "optionId": question["options"][0]["optionId"]})
    assert status == 200
    updated = payload["data"]["session"]["questions"][0]
    assert updated["feedback"] is not None
    assert updated["feedback"]["correctOptionId"]
    assert updated["feedback"]["recommendedApproach"] and updated["feedback"]["explanation"]
    # 题库里的「选项 A」在乱序后已对不上，必须已被改写
    assert "选项 A" not in updated["feedback"]["pitfallAdvice"]


def test_assessment_mode_hides_feedback_until_complete(api: CareerApi) -> None:
    session = start(api, "assessment")
    session_id = session["sessionId"]
    question = session["questions"][0]
    updated = call(api, "POST", f"/api/v1/cross-role/sessions/{session_id}/answers",
                   {"questionId": question["questionId"], "optionId": question["options"][0]["optionId"]})[1]["data"]["session"]
    assert updated["questions"][0]["feedback"] is None  # 测评模式：作答后仍不揭晓
    call(api, "POST", f"/api/v1/cross-role/sessions/{session_id}/complete")
    after = call(api, "GET", f"/api/v1/cross-role/sessions/{session_id}")[1]["data"]["session"]
    assert after["questions"][0]["feedback"] is not None


# ---------------------------------------------------------------------------- 报告


def test_report_dimensions_and_scoring(api: CareerApi) -> None:
    session = start(api, "practice")
    session_id = session["sessionId"]
    # 全部选「推荐」项：按正确答案定位（feedback 里给出 correctOptionId 的是答对那次）
    for question in session["questions"][:1]:
        call(api, "POST", f"/api/v1/cross-role/sessions/{session_id}/answers",
             {"questionId": question["questionId"], "optionId": question["options"][0]["optionId"]})
    status, payload = call(api, "POST", f"/api/v1/cross-role/sessions/{session_id}/complete")
    assert status == 200
    report = payload["data"]["report"]
    assert [item["key"] for item in report["dimensions"]] == list(DIMENSION_NAMES)
    assert all(0 <= item["score"] <= 100 for item in report["dimensions"])
    assert report["totalQuestions"] == 10
    assert report["answeredQuestions"] == 1
    assert 0 <= report["correctAnswers"] <= 1
    assert report["questionDetails"] and len(report["questionDetails"]) == 10
    assert report["disclaimer"] == "本结果用于训练反馈，不作为正式人才测评结论。"
    assert report["strengths"] and report["improvements"]
    # 未作答的题拉低总分，这是有意的（提前交卷不等于满分）
    assert report["overallScore"] <= 10


def test_unanswered_gets_partial_not_zero_on_dimensions(api: CareerApi) -> None:
    """答错不是 0 分：这道题在该维度上仍按 0.35 计入（与队友口径一致）。"""
    session = start(api, "practice")
    session_id = session["sessionId"]
    question = session["questions"][0]
    _, answered = call(api, "POST", f"/api/v1/cross-role/sessions/{session_id}/answers",
                       {"questionId": question["questionId"], "optionId": question["options"][0]["optionId"]})
    feedback = answered["data"]["session"]["questions"][0]["feedback"]
    wrong = next(option for option in question["options"] if option["optionId"] != feedback["correctOptionId"])
    call(api, "POST", f"/api/v1/cross-role/sessions/{session_id}/answers",
         {"questionId": question["questionId"], "optionId": wrong["optionId"]})
    report = call(api, "POST", f"/api/v1/cross-role/sessions/{session_id}/complete")[1]["data"]["report"]
    scored = [item for item in report["dimensions"] if item["score"] > 0]
    assert scored, "答错也应在至少一个维度上留下痕迹（0.35 权重）"
    assert report["correctAnswers"] == 0


def test_report_ready_only_after_complete_and_locked_afterwards(api: CareerApi) -> None:
    session = start(api)
    session_id = session["sessionId"]
    status, payload = call(api, "GET", f"/api/v1/cross-role/sessions/{session_id}/report")
    assert status == 409 and payload["error"]["code"] == "REPORT_NOT_READY"
    call(api, "POST", f"/api/v1/cross-role/sessions/{session_id}/complete")
    assert call(api, "GET", f"/api/v1/cross-role/sessions/{session_id}/report")[0] == 200
    question = session["questions"][0]
    status, payload = call(api, "POST", f"/api/v1/cross-role/sessions/{session_id}/answers",
                           {"questionId": question["questionId"], "optionId": question["options"][0]["optionId"]})
    assert status == 409 and payload["error"]["code"] == "SESSION_FINISHED"


# ---------------------------------------------------------------------------- 归属与删除


def test_sessions_isolated_by_cookie_and_deletable(api: CareerApi) -> None:
    owner = {"career_session": "user_000000000011"}
    other = {"career_session": "user_000000000012"}
    session_id = start(api, cookies=owner)["sessionId"]
    assert call(api, "GET", f"/api/v1/cross-role/sessions/{session_id}", cookies=owner)[0] == 200
    status, payload = call(api, "GET", f"/api/v1/cross-role/sessions/{session_id}", cookies=other)
    assert status == 404 and payload["error"]["code"] == "SESSION_NOT_FOUND"
    assert call(api, "GET", "/api/v1/cross-role/sessions", cookies=other)[1]["data"]["items"] == []
    assert call(api, "DELETE", f"/api/v1/cross-role/sessions/{session_id}", cookies=owner)[1]["data"] == {"deleted": True}
    assert call(api, "GET", f"/api/v1/cross-role/sessions/{session_id}", cookies=owner)[0] == 404
