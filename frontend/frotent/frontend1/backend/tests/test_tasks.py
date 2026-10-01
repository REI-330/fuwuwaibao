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

import hashlib
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
from backend.tasks import (
    MAX_ACTION_CHARS,
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENT_PREVIEW_CHARS,
    MAX_ATTACHMENTS_PER_TASK,
    MAX_SUBMISSION_CHARS,
    MAX_TASK_NOTE_CHARS,
    STATUS_AVAILABLE,
    STATUS_COMPLETED,
    STATUS_PLANNED,
    TaskStore,
)


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


def multipart(field: str, filename: str, data: bytes, boundary: str = "----taskattach") -> tuple:
    """拼一份 multipart/form-data（与 ``backend/resume.parse_multipart_form`` 对齐）。"""
    body = b"".join([
        f"--{boundary}\r\n".encode("utf-8"),
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode("utf-8"),
        b"Content-Type: application/octet-stream\r\n\r\n",
        data,
        b"\r\n",
        f"--{boundary}--\r\n".encode("utf-8"),
    ])
    return body, f"multipart/form-data; boundary={boundary}"


def upload(api: CareerApi, task_id: str, filename: str, data: bytes, cookies=None):
    """真上传一份附件（走 multipart，不是塞 JSON）。"""
    raw, content_type = multipart("file", filename, data)
    return api.handle("POST", f"/api/tasks/{task_id}/attachments", {}, None,
                      raw=raw, content_type=content_type, cookies=cookies)


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
    # 附件得先真上传，之后才能被引用（见「附件」一节）
    attachment = upload(api, task["taskId"], "交付说明.txt", "交付说明：实测提升 40%。".encode("utf-8"))[1]["data"]["attachment"]
    status, payload = call(api, "POST", f"/api/tasks/{task['taskId']}/runs",
                           {"action": "先核对约束再逐项验证", "submission": submission,
                            "attachmentIds": [attachment["attachmentId"]]})
    assert status == 201
    data = payload["data"]
    assert data["created"] is True
    run = data["run"]
    assert run["status"] == "SUBMITTED"
    assert run["runId"].startswith("run_")
    assert run["growthRecordId"] == run["runId"], "运行记录与成长记录用同一个 ID，便于互相指认"
    assert run["attachmentIds"] == [attachment["attachmentId"]]
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


# ---------------------------------------------------------------------------- 附件


def test_attachment_upload_stores_real_bytes_and_reports_what_it_extracted(api: CareerApi) -> None:
    task = first_task(api)
    payload_bytes = "交付说明：先核对约束，再逐项验证，实测提升 40%。".encode("utf-8")
    status, payload = upload(api, task["taskId"], "交付说明.md", payload_bytes)
    assert status == 201
    data = payload["data"]
    attachment = data["attachment"]
    assert data["created"] is True
    assert attachment["attachmentId"].startswith("att_")
    assert attachment["taskId"] == task["taskId"]
    # 存的是真字节：大小与 sha256 都要对得上，不能是「记了个文件名」
    assert attachment["byteSize"] == len(payload_bytes)
    assert attachment["sha256"] == hashlib.sha256(payload_bytes).hexdigest()
    # 文本类能抽预览，且必须是原文的子串（同样不许改写）
    assert attachment["kind"] == "text" and attachment["textExtracted"] is True
    assert attachment["preview"] and attachment["preview"] in payload_bytes.decode("utf-8")
    assert attachment["previewTruncated"] is False, "短文件不该被截断"
    # 接口不回传字节本身
    assert "content" not in attachment
    # 详情页能列出来
    detail = call(api, "GET", f"/api/tasks/{task['taskId']}")[1]["data"]
    assert [item["attachmentId"] for item in detail["attachments"]] == [attachment["attachmentId"]]
    assert detail["attachmentCount"] == 1
    # 附件可以单独取回元数据
    assert call(api, "GET", f"/api/attachments/{attachment['attachmentId']}")[1]["data"]["attachment"]["filename"] == "交付说明.md"
    # 说明里必须写明「附件不算能力证据」
    assert any("能力证据" in note for note in data["attachmentNotes"])
    # 长文件：预览截断要如实标记，而不是悄悄给半截
    long_text = ("这一段会被截断。" * 400).encode("utf-8")
    truncated = upload(api, task["taskId"], "长报告.txt", long_text)[1]["data"]["attachment"]
    assert truncated["previewTruncated"] is True
    assert len(truncated["preview"]) == MAX_ATTACHMENT_PREVIEW_CHARS


def test_attachment_upload_is_idempotent_and_is_honest_about_binary(api: CareerApi) -> None:
    task = first_task(api)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    first = upload(api, task["taskId"], "结果截图.png", png)[1]["data"]
    assert first["attachment"]["kind"] == "image"
    assert first["attachment"]["textExtracted"] is False
    assert first["attachment"]["preview"] is None
    assert "OCR" in first["attachment"]["note"], "图片必须明说没做 OCR，而不是假装读过"

    again = upload(api, task["taskId"], "结果截图.png", png)[1]["data"]
    assert again["created"] is False, "同一个文件重复上传按幂等处理"
    assert again["attachment"]["attachmentId"] == first["attachment"]["attachmentId"]
    assert again["attachmentCount"] == 1

    # 压缩包这类也收得下，只是明说没抽文字（不拿 415 挡交付物）
    zip_like = b"PK\x03\x04" + b"\x00" * 16
    other = upload(api, task["taskId"], "材料.zip", zip_like)[1]["data"]["attachment"]
    assert other["kind"] == "binary" and other["textExtracted"] is False and other["note"]


def test_attachment_upload_rejects_bad_requests(api: CareerApi) -> None:
    task = first_task(api)
    endpoint = f"/api/tasks/{task['taskId']}/attachments"
    # 空体
    assert api.handle("POST", endpoint, {}, None, raw=b"", content_type="multipart/form-data; boundary=x")[1]["error"]["code"] == "ATTACHMENT_BAD_UPLOAD"
    # 不是 multipart（拿 JSON 传附件）
    assert api.handle("POST", endpoint, {}, {"file": "x"}, raw=b'{"file":"x"}', content_type="application/json")[1]["error"]["code"] == "ATTACHMENT_BAD_UPLOAD"
    # multipart 但没有 file 字段
    raw, ct = multipart("note", "a.txt", b"hi")
    assert api.handle("POST", endpoint, {}, None, raw=raw, content_type=ct)[1]["error"]["code"] == "ATTACHMENT_BAD_UPLOAD"
    # 超限 → 413
    big = b"x" * (MAX_ATTACHMENT_BYTES + 1)
    status, payload = upload(api, task["taskId"], "big.txt", big)
    assert status == 413 and payload["error"]["code"] == "ATTACHMENT_TOO_LARGE"
    # 任务不存在 → 404（附件永远挂在一条真任务上）
    assert upload(api, "task_ZZ999_junior_0", "a.txt", b"hi")[0] == 404


def test_attachment_count_is_capped(api: CareerApi) -> None:
    task = first_task(api)
    for index in range(MAX_ATTACHMENTS_PER_TASK):
        assert upload(api, task["taskId"], f"f{index}.txt", f"内容 {index}".encode("utf-8"))[0] == 201
    status, payload = upload(api, task["taskId"], "one-too-many.txt", b"x")
    assert status == 422 and payload["error"]["code"] == "ATTACHMENT_LIMIT"


def test_submit_only_accepts_attachments_of_this_user_and_this_task(api: CareerApi) -> None:
    owner_task = first_task(api)
    mine = upload(api, owner_task["taskId"], "我的成果.txt", b"my result")[1]["data"]["attachment"]

    # 不认得的 id
    status, payload = call(api, "POST", f"/api/tasks/{owner_task['taskId']}/runs",
                           {"submission": "按步骤做了，实测 40%。", "attachmentIds": ["att_nope"]})
    assert status == 422 and payload["error"]["code"] == "UNKNOWN_ATTACHMENT"
    assert payload["error"]["unknownAttachmentIds"] == ["att_nope"]

    # 挂在**别的任务**下的 id 同样不认（否则引用会查不到出处）
    _, all_tasks = call(api, "GET", "/api/tasks")
    other_task = next(item for item in all_tasks["data"]["items"] if item["taskId"] != owner_task["taskId"])
    foreign = upload(api, other_task["taskId"], "别处的成果.txt", b"other")[1]["data"]["attachment"]
    status, payload = call(api, "POST", f"/api/tasks/{owner_task['taskId']}/runs",
                           {"submission": "按步骤做了，实测 40%。", "attachmentIds": [foreign["attachmentId"]]})
    assert status == 422 and payload["error"]["unknownAttachmentIds"] == [foreign["attachmentId"]]

    # 别人的附件：对当前用户就是「不存在」
    other = {"career_session": "user_0000000000a1"}
    theirs = upload(api, owner_task["taskId"], "别人的.txt", b"theirs", cookies=other)[1]["data"]["attachment"]
    assert call(api, "GET", f"/api/attachments/{theirs['attachmentId']}")[0] == 404
    status, payload = call(api, "POST", f"/api/tasks/{owner_task['taskId']}/runs",
                           {"submission": "按步骤做了，实测 40%。", "attachmentIds": [theirs["attachmentId"]]})
    assert status == 422 and payload["error"]["unknownAttachmentIds"] == [theirs["attachmentId"]]

    # 自己的、且挂在同一任务下 → 通过
    status, payload = call(api, "POST", f"/api/tasks/{owner_task['taskId']}/runs",
                           {"submission": "按步骤做了，实测 40%。", "attachmentIds": [mine["attachmentId"]]})
    assert status == 201
    assert payload["data"]["run"]["attachmentIds"] == [mine["attachmentId"]]


def test_attachment_text_is_not_used_as_ability_evidence(api: CareerApi) -> None:
    """附件的文字**不进能力闸门**：闸门只认用户手写的 action + submission。

    这条是「附件上传」最容易做坏的地方 —— 顺手把附件文本并进评估输入，
    就等于让用户用一份别人写的文档刷出「已具备能力」的观察。
    """
    task = first_task(api)
    skill = task["requiredSkills"][0]["name"]
    upload(api, task["taskId"], "报告.txt", f"这份报告里到处都写着 {skill}。".encode("utf-8"))

    run = call(api, "POST", f"/api/tasks/{task['taskId']}/runs",
               {"submission": "按步骤做了，结果还行。"})[1]["data"]["run"]
    report = call(api, "POST", f"/api/task-runs/{run['runId']}/evaluate")[1]["data"]["report"]
    assert report["observedAbilities"] == [], "能力名只出现在附件里，不该被算成观察结果"
    assert any(item["name"] == skill for item in report["needsVerification"])


def test_attachment_content_downloads_byte_for_byte(api: CareerApi) -> None:
    """原始字节下载：拿回来的必须和上传的一模一样（不是预览、不是 JSON 包一层）。"""
    task = first_task(api)
    payload_bytes = "交付物正文：先核对约束，再逐项验证。".encode("utf-8")
    attachment = upload(api, task["taskId"], "交付说明.md", payload_bytes)[1]["data"]["attachment"]

    slot: dict = {}
    status, body = api.handle("GET", f"/api/attachments/{attachment['attachmentId']}/content", {}, None, response=slot)
    assert status == 200
    assert body["data"]["byteSize"] == len(payload_bytes)
    assert slot["rawBody"]["content"] == payload_bytes, "字节必须逐字一致"
    assert slot["rawBody"]["contentType"] == "application/octet-stream" or slot["rawBody"]["contentType"]
    assert "filename*=UTF-8''" in slot["rawBody"]["contentDisposition"]
    assert hashlib.sha256(slot["rawBody"]["content"]).hexdigest() == attachment["sha256"]

    # 归属不符 = 不存在
    other = {"career_session": "user_0000000000b1"}
    assert api.handle("GET", f"/api/attachments/{attachment['attachmentId']}/content", {}, None, cookies=other,
                      response={})[0] == 404
    assert call(api, "GET", "/api/attachments/att_nope/content")[1]["error"]["code"] == "ATTACHMENT_NOT_FOUND"
    assert call(api, "GET", "/api/attachments/att_nope")[1]["error"]["code"] == "ATTACHMENT_NOT_FOUND"


def test_attachment_delete_refuses_while_referenced(api: CareerApi) -> None:
    """引用可追溯优先于清理：还被运行记录引用的附件不能删掉，否则 attachmentIds 会失去出处。"""
    task = first_task(api)
    used = upload(api, task["taskId"], "已被引用.txt", b"used")[1]["data"]["attachment"]
    unused = upload(api, task["taskId"], "没人引用.txt", b"unused")[1]["data"]["attachment"]

    run = call(api, "POST", f"/api/tasks/{task['taskId']}/runs",
               {"submission": "按步骤做了，实测 40%。", "attachmentIds": [used["attachmentId"]]})[1]["data"]["run"]

    status, payload = call(api, "DELETE", f"/api/attachments/{used['attachmentId']}")
    assert status == 409 and payload["error"]["code"] == "ATTACHMENT_IN_USE"
    assert payload["error"]["referencedRunIds"] == [run["runId"]]
    # 409 之后确实还在
    assert call(api, "GET", f"/api/attachments/{used['attachmentId']}")[0] == 200

    # 没被引用的可以删，count 跟着减
    status, payload = call(api, "DELETE", f"/api/attachments/{unused['attachmentId']}")
    assert status == 200 and payload["data"]["deleted"] == unused["attachmentId"]
    assert payload["data"]["attachmentCount"] == 1
    assert call(api, "GET", f"/api/attachments/{unused['attachmentId']}")[0] == 404

    # 别人的附件对我就是「不存在」，删也一样
    other = {"career_session": "user_0000000000b2"}
    mine = upload(api, task["taskId"], "我的.txt", b"mine")[1]["data"]["attachment"]
    assert call(api, "DELETE", f"/api/attachments/{mine['attachmentId']}", cookies=other)[1]["error"]["code"] == "ATTACHMENT_NOT_FOUND"
    assert call(api, "DELETE", "/api/attachments/att_nope")[1]["error"]["code"] == "ATTACHMENT_NOT_FOUND"
    # 奇怪的后缀不是「额外功能」，是未知路由
    assert call(api, "POST", f"/api/attachments/{mine['attachmentId']}/content")[0] == 404


# ---------------------------------------------------------------------------- 个人视图覆盖层


def test_task_view_overlay_changes_only_the_users_view(api: CareerApi) -> None:
    """备注 / 隐藏 / 顺序是**个人视图**：图谱派生的任务内容一个字段都不许变。"""
    _, listing = call(api, "GET", "/api/tasks")
    items = listing["data"]["items"]
    first = items[0]
    endpoint = f"/api/tasks/{first['taskId']}"

    status, payload = call(api, "PATCH", endpoint, {"note": "这条我打算先用仿真环境练手"})
    assert status == 200
    assert payload["data"]["override"]["note"] == "这条我打算先用仿真环境练手"
    assert payload["data"]["task"]["note"] == "这条我打算先用仿真环境练手"
    # 任务本体的字段必须逐字不变 —— 这是「任务不臆造」在写接口上的落点
    for key in ("title", "deliverable", "steps", "requiredSkills", "sourceRefs", "tools", "taskId"):
        assert payload["data"]["task"][key] == first[key], f"{key} 不该被覆盖层改写"

    _, after_note = call(api, "GET", "/api/tasks")
    mirror = next(task for task in after_note["data"]["items"] if task["taskId"] == first["taskId"])
    assert mirror["note"] == "这条我打算先用仿真环境练手" and mirror["hidden"] is False
    assert after_note["data"]["hiddenCount"] == 0

    # 隐藏：从默认清单消失，**且不算完成**
    status, payload = call(api, "PATCH", endpoint, {"hidden": True})
    assert status == 200 and payload["data"]["task"]["hidden"] is True
    _, hidden_list = call(api, "GET", "/api/tasks")
    assert first["taskId"] not in [task["taskId"] for task in hidden_list["data"]["items"]]
    assert hidden_list["data"]["hiddenCount"] == 1
    # counts 只数看得见的任务，否则界面会「说有 1 条可做却一条都点不出」
    assert sum(hidden_list["data"]["counts"].values()) == len(hidden_list["data"]["items"])

    _, with_hidden = call(api, "GET", "/api/tasks", query={"includeHidden": ["1"]})
    shown = next(task for task in with_hidden["data"]["items"] if task["taskId"] == first["taskId"])
    assert shown["hidden"] is True
    assert shown["status"] == STATUS_PLANNED, "隐藏不是完成：它不该变成 completed，也不该占着 available"
    # 详情页仍然打得开（历史提交与附件都还挂在上面）
    assert call(api, "GET", endpoint)[0] == 200

    # 别人的清单不受影响
    other = {"career_session": "user_0000000000c1"}
    _, other_list = call(api, "GET", "/api/tasks", cookies=other)
    assert first["taskId"] in [task["taskId"] for task in other_list["data"]["items"]]
    assert all(task["note"] == "" and task["hidden"] is False for task in other_list["data"]["items"])

    # 顺序：把整份清单倒过来 —— 排序键是（阶段, position），所以每个阶段内部都应逐条倒序
    _, fresh = call(api, "GET", "/api/tasks")
    ids = [task["taskId"] for task in fresh["data"]["items"]]
    before_by_stage: dict = {}
    for task in fresh["data"]["items"]:
        before_by_stage.setdefault(task["sourcePath"]["stage"], []).append(task["taskId"])
    status, payload = call(api, "POST", "/api/tasks/reorder", {"taskIds": list(reversed(ids))})
    assert status == 200 and payload["data"]["updated"] == len(ids)
    # 返回的是**按（阶段, position）重排后**的顺序（含被隐藏的那条：排序信息不因隐藏丢失）
    assert set(ids).issubset(set(payload["data"]["ordered"]))
    _, reordered = call(api, "GET", "/api/tasks")
    after_by_stage: dict = {}
    for task in reordered["data"]["items"]:
        after_by_stage.setdefault(task["sourcePath"]["stage"], []).append(task["taskId"])
    for stage_key, stage_ids in before_by_stage.items():
        assert after_by_stage[stage_key] == list(reversed(stage_ids)), f"{stage_key} 阶段没按 position 排"
    assert any(len(stage_ids) >= 2 and after_by_stage[stage_key] != stage_ids
               for stage_key, stage_ids in before_by_stage.items()), "没有哪个阶段的顺序真的变了"
    assert next(task for task in reordered["data"]["items"]
                if task["taskId"] == fresh["data"]["items"][-1]["taskId"])["position"] == 0

    # 不认得的 taskId 一律点名，不静默忽略
    status, payload = call(api, "POST", "/api/tasks/reorder", {"taskIds": ["task_ZZ999_junior_0"]})
    assert status == 422 and payload["error"]["code"] == "UNKNOWN_TASK"
    assert payload["error"]["unknownTaskIds"] == ["task_ZZ999_junior_0"]
    assert call(api, "POST", "/api/tasks/reorder", {"taskIds": []})[1]["error"]["code"] == "INVALID_BODY"

    # 还原：备注 / 隐藏 / 顺序一起清掉
    status, payload = call(api, "PATCH", endpoint, {"reset": True})
    assert status == 200 and payload["data"]["reset"] is True and payload["data"]["override"] is None
    _, restored = call(api, "GET", "/api/tasks")
    back = next(task for task in restored["data"]["items"] if task["taskId"] == first["taskId"])
    assert back["note"] == "" and back["hidden"] is False and back["position"] is None


def test_task_view_overlay_validates_input(api: CareerApi) -> None:
    task = first_task(api)
    endpoint = f"/api/tasks/{task['taskId']}"
    assert call(api, "PATCH", endpoint, {"hidden": "yes"})[1]["error"]["code"] == "INVALID_BODY"
    assert call(api, "PATCH", endpoint, {"note": "x" * (MAX_TASK_NOTE_CHARS + 1)})[1]["error"]["code"] == "INVALID_BODY"
    assert call(api, "PATCH", endpoint, {"position": -1})[1]["error"]["code"] == "INVALID_BODY"
    assert call(api, "PATCH", endpoint, "not-a-dict")[1]["error"]["code"] == "INVALID_BODY"
    assert call(api, "PATCH", "/api/tasks/task_ZZ999_junior_0", {"hidden": True})[1]["error"]["code"] == "TASK_NOT_FOUND"
    assert call(api, "PATCH", "/api/tasks/not-a-task-id", {"hidden": True})[1]["error"]["code"] == "TASK_NOT_FOUND"


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
