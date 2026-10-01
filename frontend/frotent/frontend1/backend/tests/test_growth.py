"""成长记录 → 待确认记忆候选 的测试（**不联网**）。

跑法：``python -m pytest backend/tests -q``。

这一条写入通道的验收点与「画像 sync」同源，外加两条它与众不同的地方：
* 记录里**没有图谱已知名词**时不写候选（宁可不写，也不把自由文本塞进记忆库）；
* 「任务行动」即使认不出名词也落一条 `已完成行动：…`（用户真的做过的事有据可依）；
* 删除记录只清**未确认**候选，已确认的记忆归用户。
"""

from __future__ import annotations

import pytest

from backend import growth
from backend.knowledge import GraphStore
from backend.memories import SKILL_PREFIX, MemoryStore, SKILL_PREFIXES
from backend.server import CareerApi, ProfileStore


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
    return CareerApi(store=store, profiles=ProfileStore(store), memories=memories)


def post(api: CareerApi, payload: dict):
    return api.handle("POST", "/api/growth-records", {}, payload)


# --------------------------------------------------------------------- 前缀白名单一致性

def test_content_prefixes_stay_in_sync_with_memory_whitelist() -> None:
    """候选内容的前缀必须能被 `MemoryStore.skill_name_from_memory` 剥掉。

    否则抽出来的「技能名」会是整句「计划学习：模型量化与部署」—— 一个图谱里不存在的技能，
    却会进匹配算式。这条断言就是防止两个文件各自漂移。
    """
    assert growth.SKILL_PREFIX == SKILL_PREFIX
    assert growth.SKILL_PREFIX in SKILL_PREFIXES
    assert growth.PLAN_PREFIX in SKILL_PREFIXES
    assert MemoryStore.skill_name_from_memory(f"{growth.SKILL_PREFIX}模型量化与部署") == "模型量化与部署"
    assert MemoryStore.skill_name_from_memory(f"{growth.PLAN_PREFIX}模型量化与部署") == "模型量化与部署"


# --------------------------------------------------------------------- 派生规则（纯函数）

def test_derive_marks_owned_vs_planned_by_kind(store: GraphStore) -> None:
    nodes = store.nodes_in("模型量化与部署")
    assert nodes and nodes[0]["kind"] == "skill"
    owned = growth.derive_candidates({"kind": "能力变化", "title": "把模型量化跑通", "recordId": "r1"}, nodes)
    planned = growth.derive_candidates({"kind": "职业方向", "title": "想学模型量化", "recordId": "r1"}, nodes)

    assert owned[0]["content"].startswith(growth.SKILL_PREFIX)
    assert planned[0]["content"].startswith(growth.PLAN_PREFIX)
    # 命中图谱名词的候选比纯文字记录更重要（+10），但不超过 100
    assert owned[0]["importance"] == growth.importance_for("能力变化", True) == 80
    assert growth.importance_for("职业方向", True) <= 100


def test_derive_confidence_bounded(store: GraphStore) -> None:
    for kind in growth.VALID_KINDS:
        assert 0 <= growth.importance_for(kind, True) <= 100
        assert 0 <= growth.importance_for(kind, False) <= 100


def test_derive_occupation_becomes_career_target(store: GraphStore) -> None:
    nodes = store.nodes_in("我想转边缘 AI 工程师")
    candidates = growth.derive_candidates({"kind": "职业方向", "title": "转岗", "recordId": "r2"}, nodes)
    contents = [entry["content"] for entry in candidates]
    assert any(entry.startswith("目标职业：") for entry in contents), contents


# --------------------------------------------------------------------- 写入通道

def test_record_writes_candidate_only(api: CareerApi) -> None:
    status, payload = post(api, {
        "kind": "任务行动",
        "title": "完成「模型量化」任务",
        "after": "把 YOLOv5 量化到 INT8",
        "source": "用户提交的行动结果",
    })
    assert status == 201
    data = payload["data"]
    assert data["created"] is True
    candidates = data["candidates"]
    assert len(candidates) == 1
    assert candidates[0]["status"] == "candidate"
    assert candidates[0]["content"] == "具备或正在学习：模型量化与部署"
    assert candidates[0]["sourceType"] == "growth_record"
    assert candidates[0]["sourceId"].startswith(data["record"]["id"] + ":")
    assert candidates[0]["triggersPending"] is True

    # 候选不进入上下文（授权闸门）——记忆库与成长记录共用同一条边界
    _, context = api.handle("GET", "/api/memories/context", {"query": ["模型量化与部署"]}, None)
    assert context["data"]["count"] == 0


def test_confirmed_candidate_reaches_context(api: CareerApi) -> None:
    _, payload = post(api, {"kind": "能力变化", "title": "学会了模型量化", "after": "模型量化与部署"})
    memory_id = payload["data"]["candidates"][0]["id"]

    api.handle("PATCH", f"/api/memories/{memory_id}", {}, {"status": "confirmed"})
    _, context = api.handle("GET", "/api/memories/context", {"query": ["模型量化与部署"]}, None)
    assert context["data"]["count"] == 1


def test_record_without_known_term_writes_nothing(api: CareerApi) -> None:
    status, payload = post(api, {"kind": "能力变化", "title": "今天心情不错", "after": "随便写写"})
    assert status == 201
    assert payload["data"]["candidates"] == []
    assert payload["data"]["note"]["reason"] == "no_known_term"
    # 认不出来就不写：记忆库里不留噪声
    _, listing = api.handle("GET", "/api/memories", {}, None)
    assert listing["data"]["count"] == 0


def test_action_record_without_known_term_keeps_one_background_candidate(api: CareerApi) -> None:
    _, payload = post(api, {"kind": "任务行动", "title": "把周报模板整理好", "after": "写完了"})
    candidates = payload["data"]["candidates"]
    assert len(candidates) == 1
    assert candidates[0]["category"] == "background"
    assert candidates[0]["content"] == "已完成行动：把周报模板整理好"


def test_record_idempotent_by_client_record_id(api: CareerApi) -> None:
    payload = {"kind": "任务行动", "title": "完成「模型量化」任务", "after": "INT8", "recordId": "client-abc"}
    first = post(api, payload)[1]["data"]
    second = post(api, payload)[1]["data"]

    assert first["created"] is True
    assert second["created"] is False
    assert second["note"]["reason"] == "record_exists"
    assert second["record"]["id"] == first["record"]["id"]
    _, listing = api.handle("GET", "/api/growth-records", {}, None)
    assert listing["data"]["count"] == 1


def test_delete_record_keeps_confirmed_and_removes_candidates(api: CareerApi) -> None:
    _, payload = post(api, {"kind": "能力变化", "title": "模型量化与部署，想做边缘 AI 工程师",
                            "after": "INT8 量化跑通"})
    record_id = payload["data"]["record"]["id"]
    candidates = {entry["category"]: entry for entry in payload["data"]["candidates"]}
    assert {"skill", "career_target"} <= set(candidates), candidates
    confirmed_id = candidates["skill"]["id"]
    pending_id = candidates["career_target"]["id"]
    api.handle("PATCH", f"/api/memories/{confirmed_id}", {}, {"status": "confirmed"})

    result = api.handle("DELETE", f"/api/growth-records/{record_id}", {}, None)[1]["data"]
    assert result["deleted"] == record_id
    assert result["keptMemoryIds"] == [confirmed_id]
    assert result["removedMemoryIds"] == [pending_id]
    # 记录没了，但它确认过的记忆仍在（删除记录 ≠ 遗忘用户确认过的事实）
    _, listing = api.handle("GET", "/api/memories", {}, None)
    ids = {item["id"] for item in listing["data"]["items"]}
    assert confirmed_id in ids and pending_id not in ids
    assert api.handle("GET", f"/api/growth-records/{record_id}", {}, None)[0] == 404


def test_record_and_candidates_are_one_transaction(api: CareerApi, monkeypatch) -> None:
    """记录与候选必须原子落库：候选写失败时记录不能留下（否则「有记录没候选」无人能查）。"""
    original = MemoryStore._insert
    calls = {"count": 0}

    def flaky(self, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("模拟第 2 条候选写失败")
        return original(self, **kwargs)

    monkeypatch.setattr(MemoryStore, "_insert", flaky)
    with pytest.raises(RuntimeError):
        post(api, {"kind": "能力变化", "title": "模型量化与部署，想做边缘 AI 工程师", "recordId": "atomic-1",
                   "after": "INT8 跑通"})

    monkeypatch.undo()
    assert calls["count"] == 2, "用例前提：确实有两条候选、第二条失败"
    _, records = api.handle("GET", "/api/growth-records", {}, None)
    assert records["data"]["count"] == 0
    _, memories = api.handle("GET", "/api/memories", {}, None)
    assert memories["data"]["count"] == 0


def test_growth_record_validation(api: CareerApi) -> None:
    assert post(api, {"kind": "不存在的类别", "title": "x"})[0] == 400
    assert post(api, {"kind": "任务行动", "title": "   "})[0] == 400
    assert post(api, None)[0] == 400
    _, payload = api.handle("GET", "/api/growth-records", {"kind": ["不存在的类别"]}, None)
    assert payload["error"]["code"] == "INVALID_GROWTH_KIND"


# --------------------------------------------------------------------- 分页

def test_growth_records_paginate_by_cursor(api: CareerApi) -> None:
    """时间线分页：位移分页必须**不重不漏**，且 `total` 是筛选后的总条数。"""
    for index in range(7):
        status, payload = post(api, {
            "kind": "能力变化",
            "title": f"第 {index} 条：模型量化与部署",
            "after": f"第 {index} 次记录",
            "recordId": f"page-{index}",
            # 时间戳递增（秒），保证排序唯一可预期
            "occurredAt": f"2026-10-0{index + 1}T00:00:00Z",
        })
        assert status == 201

    _, first = api.handle("GET", "/api/growth-records", {"limit": ["3"]}, None)
    page1 = first["data"]
    assert page1["count"] == 3 and page1["total"] == 7
    assert page1["hasMore"] is True and page1["nextCursor"] == "3"
    assert page1["limit"] == 3 and page1["offset"] == 0

    _, second = api.handle("GET", "/api/growth-records", {"limit": ["3"], "cursor": [page1["nextCursor"]]}, None)
    page2 = second["data"]
    assert page2["count"] == 3 and page2["offset"] == 3
    assert page2["hasMore"] is True and page2["nextCursor"] == "6"

    _, third = api.handle("GET", "/api/growth-records", {"limit": ["3"], "cursor": [page2["nextCursor"]]}, None)
    page3 = third["data"]
    assert page3["count"] == 1 and page3["hasMore"] is False and page3["nextCursor"] is None

    ids = [item["id"] for item in page1["items"] + page2["items"] + page3["items"]]
    assert len(ids) == 7 and len(set(ids)) == 7, "分页必须不重不漏"
    # 排序：occurredAt 倒序
    assert [item["occurredAt"] for item in page1["items"]] == sorted(
        [item["occurredAt"] for item in page1["items"]], reverse=True)

    # 分类筛选下的 total 只算这一类
    post(api, {"kind": "任务行动", "title": "完成实践任务", "after": "做了", "recordId": "page-other"})
    _, filtered = api.handle("GET", "/api/growth-records", {"kind": ["任务行动"], "limit": ["3"]}, None)
    assert filtered["data"]["total"] == 1 and filtered["data"]["hasMore"] is False

    # 不传 limit：保持旧行为（一次给全部），且 hasMore 为 false
    _, all_records = api.handle("GET", "/api/growth-records", {}, None)
    assert all_records["data"]["count"] == 8 and all_records["data"]["hasMore"] is False
    assert all_records["data"]["nextCursor"] is None

    # 非法分页参数要明确报错，而不是悄悄当成 0
    assert api.handle("GET", "/api/growth-records", {"limit": ["abc"]}, None)[1]["error"]["code"] == "INVALID_GROWTH_PAGE"
    assert api.handle("GET", "/api/growth-records", {"cursor": ["-1"]}, None)[1]["error"]["code"] == "INVALID_GROWTH_PAGE"
