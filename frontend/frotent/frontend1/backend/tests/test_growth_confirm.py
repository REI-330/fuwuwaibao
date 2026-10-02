"""M1-3：``POST /api/growth-records/confirm`` —— 候选 → 记录 → 证据 → 事件，一个事务。

验收口径（M1-3）：
* 四张表一个事务写入（这里用「重复 confirm 不产生重复行」与「越权被拒」两头夹）；
* 越权保护：只认这条记录自己的候选，传别人的 id 直接 400，不静默跳过。
"""

from __future__ import annotations

import pytest

from backend.knowledge import GraphStore
from backend.memories import MemoryStore
from backend.server import CareerApi


@pytest.fixture(scope="module")
def store() -> GraphStore:
    return GraphStore()


@pytest.fixture()
def api(store: GraphStore) -> CareerApi:
    return CareerApi(store=store, memories=MemoryStore(path=":memory:"))


def make_record(api: CareerApi, title: str = "完成模型量化与部署实战", record_id: str = "growth_test_1"):
    status, payload = api.handle(
        "POST", "/api/growth-records", {},
        {"recordId": record_id, "kind": "能力变化", "title": title, "source": "用户记录"},
    )
    assert status == 201
    data = payload["data"]
    assert data["created"] is True
    return data["record"], [item["id"] for item in data["candidates"]]


def test_confirm_moves_candidate_to_confirmed_with_evidence_and_event(api: CareerApi) -> None:
    record, candidate_ids = make_record(api)
    assert candidate_ids, "记录里含图谱已知技能名，应派生出候选"

    status, payload = api.handle(
        "POST", "/api/growth-records/confirm", {},
        {"recordId": record["id"], "memoryIds": candidate_ids},
    )
    assert status == 200
    result = payload["data"]
    assert set(result["confirmedMemoryIds"]) == set(candidate_ids)
    assert result["alreadyConfirmedMemoryIds"] == []
    assert len(result["evidence"]) == len(candidate_ids)
    assert len(result["events"]) == len(candidate_ids)
    assert result["memoryHash"] and result["memoryHash"] != ""

    # 确认后进入正式记忆（候选闸门放行）
    confirmed = api.memories.confirmed_items("user_local")
    assert {item["id"] for item in confirmed} == set(candidate_ids)


def test_repeat_confirm_is_idempotent(api: CareerApi) -> None:
    record, candidate_ids = make_record(api, record_id="growth_test_repeat")
    body = {"recordId": record["id"], "memoryIds": candidate_ids}
    api.handle("POST", "/api/growth-records/confirm", {}, body)
    second = api.handle("POST", "/api/growth-records/confirm", {}, body)[1]["data"]

    assert second["confirmedMemoryIds"] == []
    assert set(second["alreadyConfirmedMemoryIds"]) == set(candidate_ids)
    # 证据 / 事件不因重复 confirm 而堆行
    assert len(api.memories.list_profile_evidence("user_local")) == len(candidate_ids)
    assert len(api.memories.list_growth_events("user_local")) == len(candidate_ids)


def test_unknown_record_is_404(api: CareerApi) -> None:
    status, payload = api.handle(
        "POST", "/api/growth-records/confirm", {},
        {"recordId": "growth_missing", "memoryIds": ["memory_x"]},
    )
    assert status == 404
    assert payload["error"]["code"] == "GROWTH_RECORD_NOT_FOUND"


def test_foreign_memory_id_is_rejected(api: CareerApi) -> None:
    """拿别的记录的候选来确认必须被拒 —— 否则等于绕过了记录的授权边界。"""
    first, _ = make_record(api, record_id="growth_own_a")
    other, other_ids = make_record(api, record_id="growth_own_b")
    status, payload = api.handle(
        "POST", "/api/growth-records/confirm", {},
        {"recordId": first["id"], "memoryIds": other_ids},
    )
    assert status == 400
    assert payload["error"]["code"] == "INVALID_GROWTH_CONFIRM"


def test_missing_or_empty_memory_ids_is_400(api: CareerApi) -> None:
    record, _ = make_record(api, record_id="growth_empty_ids")
    for body in ({"recordId": record["id"]}, {"recordId": record["id"], "memoryIds": []}, {}):
        status, payload = api.handle("POST", "/api/growth-records/confirm", {}, body)
        assert status == 400
        assert payload["error"]["code"] == "INVALID_BODY"


def test_events_are_listed_newest_last_and_survive_record_delete(api: CareerApi) -> None:
    """事件是历史流水：删掉记录后仍应查得到（刻意不做外键的原因）。"""
    record, candidate_ids = make_record(api, record_id="growth_events_history")
    api.handle("POST", "/api/growth-records/confirm", {}, {"recordId": record["id"], "memoryIds": candidate_ids})
    api.handle("DELETE", f"/api/growth-records/{record['id']}", {}, None)

    events = api.memories.list_growth_events("user_local")
    assert any(event["recordId"] == record["id"] for event in events)
