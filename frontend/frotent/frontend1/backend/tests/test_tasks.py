"""任务实践（`/api/tasks*`）的测试（**不联网**）。

跑法：``python -m pytest backend/tests -q``。

钉住的是这条链路最容易被做坏的地方：

* **任务不臆造**：任务来自路径引擎（图谱 task→skill 边），图谱没有的字段（难度、任务级学时）
  如实为 ``null`` 并出现在 ``unavailableFields`` 里；
* **状态口径**：由「路径阶段顺序 + 已提交的运行记录」共同决定，不是随机或写死的；
* **提交只产出待确认候选**：写成长记录 + ``candidate``，**没有任何一条落到 ``confirmed``**；
* **``requestId`` 幂等**：重复提交不会多出一条成长记录或多一批候选（先查再写）；
* **评估只给反馈**：规则版永远可用；模型声称的「观察到的能力」必须**引用了提交原文里真实存在的片段**，
  否则降级进 ``needsVerification`` —— 不把模型的话当证据；
* **归属隔离**：别人的运行记录不能评估，也不会出现在你的清单里。
"""

from __future__ import annotations

import json
from typing import List

import pytest

from backend.cross_role import CrossRoleStore
from backend.interviews import InterviewStore
from backend.llm import LlmClient, LlmConfig
from backend.memories import MemoryStore
from backend.resume_store import ResumeStore
from backend.server import CareerApi, ProfileStore
from backend.knowledge import GraphStore
from backend.tasks import MAX_ACTION_CHARS, MAX_SUBMISSION_CHARS, STATUS_AVAILABLE, STATUS_COMPLETED, STATUS_PLANNED, TaskStore


class ScriptedTransport:
    """按 prompt 的 ``task`` 分派，返回预先写好的评估 JSON（绝不联网）。"""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.bodies: List[dict] = []

    def __call__(self, url, body, headers, timeout):
        prompt = json.loads(body.decode("utf-8"))
        self.bodies.append(json.loads(prompt["messages"][0]["content"]))
        content = json.dumps(self.payload, ensure_ascii=False)
        return 200, json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}, ensure_ascii=False)


@pytest.fixture(scope="module")
def store() -> GraphStore:
    return GraphStore()


@pytest.fixture()
def memories(store: GraphStore) -> MemoryStore:
    instance = MemoryStore(":memory:", vocabulary=store.terms_in)
    yield instance
    instance.close()


def build_api(store, memories, llm=None) -> CareerApi:
    client = llm or LlmClient()
    return CareerApi(
        store=store,
        profiles=ProfileStore(store),
        memories=memories,
        interviews=InterviewStore(":memory:", llm=LlmClient()),
        cross_role=CrossRoleStore(":memory:"),
        resumes=ResumeStore(":memory:"),
        tasks=TaskStore(":memory:", llm=client),
    )


@pytest.fixture()
def api(store: GraphStore, memories: MemoryStore) -> CareerApi:
    return build_api(store, memories)


def call(api: CareerApi, method: str, path: str, body=None, query=None, cookies=None):
    return api.handle(method, path, query or {}, body, cookies=cookies)


def first_task(api: CareerApi, cookies=None) -> dict:
    _, payload = call(api, "GET", "/api/tasks", cookies=cookies)
    return payload["data"]["items"][0]


# ---------------------------------------------------------------------------- 任务派生


def test_tasks_are_derived_from_the_graph_not_invented(api: CareerApi) -> None:
    status, payload = call(api, "GET", "/api/tasks")
    assert status == 200
    data = payload["data"]
    assert data["count"] > 0
    assert data["occupationSource"] == "catalog"  # 没有画像/参数时用目录第一个，且如实回传
    assert data["occupation"]["occupationId"]
    task = data["items"][0]
    # 图谱里没有的字段：如实为 null，且列进 unavailableFields
    assert task["difficulty"] is None
    assert task["estimatedHours"] is None
    assert set(task["unavailableFields"]) == {"difficulty", "estimatedHours"}
    # 有的字段必须是真的
    assert task["title"] and task["taskId"].startswith("task_")
    assert task["sourcePath"]["stage"] in ("junior", "intermediate", "advanced")
    assert task["sourcePath"]["stageName"] and task["sourcePath"]["period"]
    assert task["sourceRefs"], "图谱任务边本来就带 sourceRefs，空的话说明没接上"
    assert task["stageEstimatedHours"] >= 0
    assert all(skill["name"] for skill in task["requiredSkills"])
    # 清单里必须给出「哪些字段图谱没有」的说明，而不是让前端自己猜
    assert any("难度" in note for note in data["notes"])


def test_task_status_follows_stage_order_and_submissions(api: CareerApi) -> None:
    _, payload = call(api, "GET", "/api/tasks")
    items = payload["data"]["items"]
    assert all(item["status"] in (STATUS_AVAILABLE, STATUS_PLANNED) for item in items)
    assert any(item["status"] == STATUS_AVAILABLE for item in items), "第一个未完成阶段的任务应当是 available"

    target = next(item for item in items if item["status"] == STATUS_AVAILABLE)
    call(api, "POST", f"/api/tasks/{target['taskId']}/runs", {"submission": "按步骤做了，结果如下：实测 40%。"})
    _, after = call(api, "GET", "/api/tasks")
    states = {item["taskId"]: item["status"] for item in after["data"]["items"]}
    assert states[target["taskId"]] == STATUS_COMPLETED
    assert after["data"]["counts"][STATUS_COMPLETED] == 1


def test_task_list_filters_and_errors(api: CareerApi) -> None:
    status, payload = call(api, "GET", "/api/tasks", query={"status": [STATUS_AVAILABLE]})
    assert status == 200 and all(item["status"] == STATUS_AVAILABLE for item in payload["data"]["items"])
    assert call(api, "GET", "/api/tasks", query={"status": ["bogus"]})[1]["error"]["code"] == "INVALID_STATUS"
    assert call(api, "GET", "/api/tasks", query={"occupation": ["NOPE"]})[1]["error"]["code"] == "OCCUPATION_NOT_FOUND"

    occupation = payload["data"]["occupation"]["occupationId"]
    _, scoped = call(api, "GET", "/api/tasks", query={"occupation": [occupation]})
    assert scoped["data"]["occupationSource"] == "query"
    assert scoped["data"]["occupation"]["occupationId"] == occupation

    assert call(api, "GET", "/api/tasks/task_ZZ999_junior_0")[1]["error"]["code"] == "TASK_NOT_FOUND"
    assert call(api, "GET", "/api/tasks/not-a-task-id")[1]["error"]["code"] == "TASK_NOT_FOUND"
    assert call(api, "POST", "/api/tasks/task_ZZ999_junior_0/runs", {"submission": "x"})[1]["error"]["code"] == "TASK_NOT_FOUND"


# ---------------------------------------------------------------------------- 提交


def test_submit_writes_growth_record_and_candidates_only(api: CareerApi, memories: MemoryStore) -> None:
    task = first_task(api)
    skill_name = task["requiredSkills"][0]["name"] if task["requiredSkills"] else "模型量化与部署"
    submission = f"首先我核对了约束，接着按步骤验证，用到了 {skill_name}，例如实测指标提升 40%；最后写了交付说明。"
    status, payload = call(api, "POST", f"/api/tasks/{task['taskId']}/runs",
                           {"action": "先核对约束再逐项验证", "submission": submission, "attachmentIds": ["a1"]})
    assert status == 201
    data = payload["data"]
    assert data["created"] is True
    run = data["run"]
    assert run["status"] == "SUBMITTED"
    assert run["runId"].startswith("run_")
    assert run["growthRecordId"] == run["runId"], "运行记录与成长记录用同一个 ID，便于互相指认"
    assert run["attachmentIds"] == ["a1"]
    assert data["growthRecord"]["kind"] == "任务行动"
    assert data["growthRecord"]["id"] == run["runId"]

    # 候选：只可能是 candidate，绝不出现 confirmed
    assert data["candidates"], "提交里命中了图谱名词，应该派生候选"
    assert all(item["status"] == "candidate" for item in data["candidates"])
    assert all(item["sourceType"] == "growth_record" for item in data["candidates"])
    stored = memories.list_items("user_local")
    assert stored and all(item["status"] == "candidate" for item in stored)
    assert len(run["candidateIds"]) == len(data["candidates"])
    # 提交前没进注入上下文，提交后（未确认）也不该进
    _, context = call(api, "GET", "/api/memories/context", query={"query": [skill_name]})
    assert context["data"]["recalled"] == []


def test_submit_is_idempotent_by_request_id(api: CareerApi, memories: MemoryStore) -> None:
    task = first_task(api)
    body = {"submission": "按步骤做了，用到 模型量化与部署，实测 40%。", "requestId": "tk-same"}
    first = call(api, "POST", f"/api/tasks/{task['taskId']}/runs", body)[1]["data"]
    second = call(api, "POST", f"/api/tasks/{task['taskId']}/runs", body)[1]["data"]
    assert first["run"]["runId"] == second["run"]["runId"]
    assert second["created"] is False
    assert second["candidateNote"]["reason"] == "duplicate_request"
    # 关键：**不能**多出一条成长记录（先查 requestId 再写，就是为了这个）
    _, records = call(api, "GET", "/api/growth-records")
    assert records["data"]["count"] == 1
    assert len(memories.list_items("user_local")) == len(first["candidates"])


def test_submit_rejects_bad_input(api: CareerApi) -> None:
    task = first_task(api)
    endpoint = f"/api/tasks/{task['taskId']}/runs"
    assert call(api, "POST", endpoint, {"submission": "   "})[1]["error"]["code"] == "INVALID_BODY"
    assert call(api, "POST", endpoint, {"submission": "x" * (MAX_SUBMISSION_CHARS + 1)})[1]["error"]["code"] == "INVALID_BODY"
    assert call(api, "POST", endpoint, {"submission": "x", "action": "y" * (MAX_ACTION_CHARS + 1)})[1]["error"]["code"] == "INVALID_BODY"
    assert call(api, "POST", endpoint, {"submission": "x", "attachmentIds": "nope"})[1]["error"]["code"] == "INVALID_BODY"
    assert call(api, "POST", endpoint, "not-a-dict")[1]["error"]["code"] == "INVALID_BODY"


# ---------------------------------------------------------------------------- 评估


def test_evaluate_fallback_only_claims_what_is_in_the_submission(api: CareerApi) -> None:
    task = first_task(api)
    skill_name = task["requiredSkills"][0]["name"] if task["requiredSkills"] else "模型量化与部署"
    submission = f"首先说明背景，其次用到了 {skill_name}，例如实测数据 40%；最后复盘了风险。"
    run = call(api, "POST", f"/api/tasks/{task['taskId']}/runs", {"submission": submission})[1]["data"]["run"]
    status, payload = call(api, "POST", f"/api/task-runs/{run['runId']}/evaluate")
    assert status == 200
    report = payload["data"]["report"]
    assert report["provider"] == "fallback"
    assert report["disclaimer"]
    assert 0 <= report["score"] <= 95
    # 观察到的能力：必须是这条任务要求的能力，且 evidence 是用户原文里的逐字片段
    required_names = {skill["name"] for skill in task["requiredSkills"]}
    source = f"{run['action']}\n{submission}"
    for item in report["observedAbilities"]:
        assert item["name"] in required_names
        assert item["evidence"], "没有出处的能力观察不该出现"
        assert item["evidence"] in source
    observed_names = {item["name"] for item in report["observedAbilities"]}
    assert observed_names, "提交里明确用到了要求能力，应该被认出来"
    assert skill_name in observed_names
    # 仍需验证：至少包含证据要求与步骤提示
    kinds = {item["kind"] for item in report["needsVerification"]}
    assert {"evidence", "steps"} & kinds
    # 没提到的要求能力应当进 needsVerification，而不是被算成已掌握
    missing = [skill["name"] for skill in task["requiredSkills"] if skill["name"] not in observed_names]
    assert all(name not in observed_names for name in missing)
    # 幂等：重复评估返回同一份
    again = call(api, "POST", f"/api/task-runs/{run['runId']}/evaluate")[1]["data"]["report"]
    assert again["score"] == report["score"]
    # 评估不会把任何东西写成已确认
    assert all(item["status"] == "candidate" for item in call(api, "GET", "/api/memories")[1]["data"]["items"])


def test_evaluate_short_submission_is_flagged(api: CareerApi) -> None:
    task = first_task(api)
    run = call(api, "POST", f"/api/tasks/{task['taskId']}/runs", {"submission": "做了。"})[1]["data"]["run"]
    report = call(api, "POST", f"/api/task-runs/{run['runId']}/evaluate")[1]["data"]["report"]
    assert any(item["kind"] == "detail" for item in report["needsVerification"])
    assert report["observedAbilities"] == [], "没提到任何要求能力时，宁可空着也不硬判"


def test_model_evaluation_must_quote_the_submission(store: GraphStore, memories: MemoryStore) -> None:
    # 先只用规则版拿到任务清单（避免多打一次模型）
    plain = build_api(store, memories)
    task = first_task(plain)
    skill = task["requiredSkills"][0]["name"]

    payload = {
        "summary": "模型总结",
        "strengths": ["模型亮点"],
        "improvements": ["模型建议"],
        "observedAbilities": [
            {"name": skill, "evidence": f"然后用 {skill} 验证"},
            {"name": skill, "evidence": "这句在提交里根本不存在"},
            {"name": "编造的能力", "evidence": "首先核对约束"},
        ],
        "needsVerification": [{"kind": "detail", "name": "模型待验证", "why": "模型说的"}],
    }
    transport = ScriptedTransport(payload)
    api = build_api(store, memories, llm=LlmClient(
        LlmConfig(base_url="http://llm.invalid", api_key="k", model="m", source="test"),
        transport=transport, sleep=lambda _s: None))
    submission = f"首先核对约束，然后用 {skill} 验证，例如实测 40%，最后复盘。"
    run = call(api, "POST", f"/api/tasks/{task['taskId']}/runs", {"submission": submission})[1]["data"]["run"]
    report = call(api, "POST", f"/api/task-runs/{run['runId']}/evaluate")[1]["data"]["report"]
    assert report["provider"] == "llm"
    assert report["summary"] == "模型总结"
    # 只有「命中要求能力 + evidence 逐字出现在提交里」的那一条被采纳
    assert [item["name"] for item in report["observedAbilities"]] == [skill]
    assert report["observedAbilities"][0]["evidence"] == f"然后用 {skill} 验证"
    rejected = [item for item in report["needsVerification"] if "未采纳" in item["why"]]
    assert len(rejected) == 2
    assert any(item["name"] == "编造的能力" for item in rejected)
    # prompt 里必须写明这几条约束
    sent = transport.bodies[0]
    assert any("逐字存在" in rule for rule in sent["rules"])
    assert any("逐字" in rule and "requiredSkills" in rule for rule in sent["rules"])
    assert sent["userSubmission"] == submission


def test_model_silence_keeps_rule_observations(store: GraphStore, memories: MemoryStore) -> None:
    """模型一条观察都没给（或全被拒），规则版找到的那条仍然要在 —— 并集，不是替换。"""
    plain = build_api(store, memories)
    task = first_task(plain)
    skill = task["requiredSkills"][0]["name"]
    transport = ScriptedTransport({"summary": "模型说没啥可说的", "observedAbilities": [], "needsVerification": []})
    api = build_api(store, memories, llm=LlmClient(
        LlmConfig(base_url="http://llm.invalid", api_key="k", model="m", source="test"),
        transport=transport, sleep=lambda _s: None))
    submission = f"先核对约束，然后用 {skill} 验证，实测 40%。"
    run = call(api, "POST", f"/api/tasks/{task['taskId']}/runs", {"submission": submission})[1]["data"]["run"]
    report = call(api, "POST", f"/api/task-runs/{run['runId']}/evaluate")[1]["data"]["report"]
    assert report["provider"] == "llm"
    assert [item["name"] for item in report["observedAbilities"]] == [skill]
    assert report["coverage"] > 0


# ---------------------------------------------------------------------------- 归属


def test_runs_are_isolated_by_cookie(api: CareerApi) -> None:
    owner = {"career_session": "user_000000000021"}
    other = {"career_session": "user_000000000022"}
    task = first_task(api, cookies=owner)
    run = call(api, "POST", f"/api/tasks/{task['taskId']}/runs",
               {"submission": "按步骤做了，实测 40%。"}, cookies=owner)[1]["data"]["run"]

    # 任务清单是图谱派生的、共享的；但**状态**与运行记录按用户隔离
    _, other_list = call(api, "GET", "/api/tasks", cookies=other)
    assert other_list["data"]["counts"][STATUS_COMPLETED] == 0
    assert call(api, "GET", f"/api/tasks/{task['taskId']}", cookies=other)[1]["data"]["runCount"] == 0
    assert call(api, "GET", f"/api/tasks/{task['taskId']}", cookies=owner)[1]["data"]["runCount"] == 1

    status, payload = call(api, "POST", f"/api/task-runs/{run['runId']}/evaluate", cookies=other)
    assert status == 404 and payload["error"]["code"] == "TASK_RUN_NOT_FOUND"
    assert call(api, "GET", "/api/memories", cookies=other)[1]["data"]["count"] == 0
    assert call(api, "GET", "/api/growth-records", cookies=other)[1]["data"]["count"] == 0
    assert call(api, "POST", "/api/task-runs/run_nope/evaluate")[0] == 404
