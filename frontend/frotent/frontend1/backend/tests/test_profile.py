"""M1-1：画像 / 证据 / 成长事件的持久化（stdlib + SQLite）。

此前 ``ProfileStore`` 是进程内内存，后端一重启画像就归零 —— 简历解析出来的草稿
全都白做。这里钉住三件事：

1. 画像写进 ``career.db`` 的 ``profiles`` 表后，**换一个 CareerApi 实例**（等价于
   进程重启）仍读得到；
2. 画像 upsert 只留一行（同一 user 不堆版本行）；
3. 证据与事件写接口按内容幂等（重复调用不产生重复行）。

全部走临时文件库，不碰仓库里的 ``backend/career.db``。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.knowledge import GraphStore
from backend.memories import MemoryStore
from backend.server import SESSION_COOKIE, CareerApi, ProfileStore


@pytest.fixture(scope="module")
def store() -> GraphStore:
    return GraphStore()


def build_api(store: GraphStore, db: Path) -> CareerApi:
    """每次都新建 MemoryStore（新 sqlite 连接）+ CareerApi —— 等价于进程重启。"""
    memories = MemoryStore(path=str(db))
    return CareerApi(store=store, memories=memories)


def test_profile_survives_new_api_instance(store: GraphStore, tmp_path: Path) -> None:
    db = tmp_path / "career-test.db"
    first = build_api(store, db)
    status, payload = first.handle(
        "PUT",
        "/api/profile",
        {},
        {"identity": "在校生", "school": "浙江大学", "major": "自动化", "skills": "C语言,STM32",
         "directions": "嵌入式开发、边缘AI", "source": "manual"},
    )
    assert status == 200
    assert payload["data"]["profile"]["profileVersion"] == 1
    first.handle("POST", "/api/profile/confirm", {}, None)

    # 换一个实例（新进程）：画像必须还在，且状态是 confirmed
    second = build_api(store, db)
    status, payload = second.handle("GET", "/api/profile", {}, None)
    assert status == 200
    profile = payload["data"]["profile"]
    assert profile["status"] == "confirmed"
    assert profile["school"] == "浙江大学"
    assert [s["name"] for s in profile["skills"]] == ["C语言", "STM32"]
    assert profile["candidateOccupationIds"] == ["AI001", "AI004"]


def test_profile_upsert_keeps_single_row(store: GraphStore, tmp_path: Path) -> None:
    db = tmp_path / "career-upsert.db"
    api = build_api(store, db)
    for major in ("自动化", "电子工程", "计算机"):
        api.handle("PUT", "/api/profile", {}, {"major": major, "source": "manual"})

    rows = api.memories._rows("SELECT user_id FROM profiles")  # noqa: SLF001（测试内省）
    assert len(rows) == 1
    assert api.handle("GET", "/api/profile", {}, None)[1]["data"]["profile"]["major"] == "计算机"
    # 版本号按「写入次数」递增，且重启后仍在
    assert api.handle("GET", "/api/profile", {}, None)[1]["data"]["profile"]["profileVersion"] == 3


def test_profile_store_without_memories_stays_in_memory(store: GraphStore, tmp_path: Path) -> None:
    """没有 memories 时退回纯内存：这是单测里最常见的构造方式，必须保持可用。"""
    profiles = ProfileStore(store)
    assert profiles.get()["userId"] == "user_local"
    saved = profiles.save({"major": "自动化", "source": "manual"})
    assert saved["profileVersion"] == 1
    assert profiles.get()["major"] == "自动化"


def test_profile_evidence_idempotent(store: GraphStore, tmp_path: Path) -> None:
    db = tmp_path / "career-evidence.db"
    memories = MemoryStore(path=str(db))
    kwargs = dict(source_type="resume", source_id="resume_abc", field="school",
                  value="浙江大学", locator="chars[12,16]")
    first = memories.add_profile_evidence("user_local", **kwargs)
    again = memories.add_profile_evidence("user_local", **kwargs)
    assert first["id"] == again["id"]
    assert len(memories.list_profile_evidence("user_local")) == 1

    # 换一个前端字段就是另一条证据，不会被上一条吞掉
    memories.add_profile_evidence("user_local", source_type="resume", source_id="resume_abc",
                                 field="major", value="自动化")
    assert len(memories.list_profile_evidence("user_local")) == 2


def test_growth_event_idempotent(store: GraphStore, tmp_path: Path) -> None:
    db = tmp_path / "career-events.db"
    memories = MemoryStore(path=str(db))
    first = memories.add_growth_event("user_local", action="confirm_candidate", record_id="growth_1",
                                      memory_id="memory_x", detail={"field": "skill"})
    again = memories.add_growth_event("user_local", action="confirm_candidate", record_id="growth_1",
                                      memory_id="memory_x", detail={"field": "skill"})
    assert first["id"] == again["id"]
    assert len(memories.list_growth_events("user_local")) == 1

    memories.add_growth_event("user_local", action="confirm_candidate", record_id="growth_1",
                              memory_id="memory_y", detail={"field": "skill"})
    assert len(memories.list_growth_events("user_local")) == 2


# ----------------------------------------------------------- 画像历史快照（第七轮）
#
# 这一组钉住「回看上周那一刻的档案」：每次写入 / 确认留一份**只增不改**的快照，
# 且同一 (version, status) 重复写是幂等的（重复 confirm 不堆快照）。


def test_profile_history_survives_new_instance(store: GraphStore, tmp_path: Path) -> None:
    db = tmp_path / "career-history.db"
    first = build_api(store, db)
    first.handle(
        "PUT",
        "/api/profile",
        {},
        {"identity": "在校生", "school": "浙江大学", "major": "自动化",
         "skills": "C语言,STM32", "source": "manual"},
    )
    first.handle("POST", "/api/profile/confirm", {}, None)

    status, payload = first.handle("GET", "/api/profile/history", {}, None)
    assert status == 200
    items = payload["data"]["items"]
    assert payload["data"]["total"] == 2
    draft = next(item for item in items if item["status"] == "draft")
    confirmed = next(item for item in items if item["status"] == "confirmed")
    assert draft["profileVersion"] == 1
    assert draft["summary"]["skills"] == ["C语言", "STM32"]
    assert confirmed["snapshotId"] != draft["snapshotId"]

    # 换一个实例（等价于进程重启）：历史仍在，且能读到「那一刻的完整画像」
    second = build_api(store, db)
    status, payload = second.handle("GET", f"/api/profile/history/{draft['snapshotId']}", {}, None)
    assert status == 200
    snapshot = payload["data"]["profile"]
    assert snapshot["status"] == "draft"
    assert snapshot["major"] == "自动化"
    assert [skill["name"] for skill in snapshot["skills"]] == ["C语言", "STM32"]


def test_profile_snapshots_are_append_only_and_confirm_is_idempotent(store: GraphStore, tmp_path: Path) -> None:
    api = build_api(store, tmp_path / "career-history-append.db")
    api.handle("PUT", "/api/profile", {}, {"major": "自动化", "source": "manual"})
    api.handle("POST", "/api/profile/confirm", {}, None)
    api.handle("POST", "/api/profile/confirm", {}, None)  # 重复 confirm

    data = api.handle("GET", "/api/profile/history", {}, None)[1]["data"]
    assert data["total"] == 2, "重复 confirm 不该堆出第三份快照"

    # 再写一版：只新增，旧的不动（append-only）
    api.handle("PUT", "/api/profile", {}, {"major": "电子工程", "source": "manual"})
    data = api.handle("GET", "/api/profile/history", {}, None)[1]["data"]
    assert data["total"] == 3
    assert data["items"][0]["profileVersion"] == 2
    assert data["items"][0]["status"] == "draft"
    assert data["items"][0]["summary"]["major"] == "电子工程"


def test_profile_history_paginates_by_cursor(store: GraphStore, tmp_path: Path) -> None:
    api = build_api(store, tmp_path / "career-history-page.db")
    for index in range(5):
        api.handle("PUT", "/api/profile", {}, {"major": f"M{index}", "source": "manual"})

    first = api.handle("GET", "/api/profile/history", {"limit": ["2"]}, None)[1]["data"]
    assert first["count"] == 2 and first["total"] == 5
    assert first["hasMore"] is True and first["nextCursor"] == "2"

    second = api.handle("GET", "/api/profile/history", {"limit": ["2"], "cursor": ["2"]}, None)[1]["data"]
    assert second["count"] == 2 and second["nextCursor"] == "4"

    third = api.handle("GET", "/api/profile/history", {"limit": ["2"], "cursor": ["4"]}, None)[1]["data"]
    assert third["count"] == 1 and third["hasMore"] is False and third["nextCursor"] is None


def test_profile_history_validates_pagination(store: GraphStore, tmp_path: Path) -> None:
    api = build_api(store, tmp_path / "career-history-bad.db")
    status, payload = api.handle("GET", "/api/profile/history", {"limit": ["x"]}, None)
    assert status == 400 and payload["error"]["code"] == "INVALID_PROFILE_PAGE"
    status, payload = api.handle("GET", "/api/profile/history", {"cursor": ["-1"]}, None)
    assert status == 400 and payload["error"]["code"] == "INVALID_PROFILE_PAGE"


def test_profile_snapshot_not_found_and_user_isolation(store: GraphStore, tmp_path: Path) -> None:
    api = build_api(store, tmp_path / "career-history-scope.db")
    alice = {SESSION_COOKIE: "user_aaaaaaaaaaaa"}
    bob = {SESSION_COOKIE: "user_bbbbbbbbbbbb"}
    api.handle("PUT", "/api/profile", {}, {"major": "自动化", "source": "manual"}, cookies=alice)
    mine = api.handle("GET", "/api/profile/history", {}, None, cookies=alice)[1]["data"]["items"][0]["snapshotId"]

    # 别人看不到我的快照，拿我的 id 也是 404（不泄露「这个 id 存在但不是你的」）
    assert api.handle("GET", "/api/profile/history", {}, None, cookies=bob)[1]["data"]["total"] == 0
    status, payload = api.handle("GET", f"/api/profile/history/{mine}", {}, None, cookies=bob)
    assert status == 404 and payload["error"]["code"] == "PROFILE_SNAPSHOT_NOT_FOUND"

    # 自己读自己是 200；未知 id 是 404
    assert api.handle("GET", f"/api/profile/history/{mine}", {}, None, cookies=alice)[0] == 200
    status, payload = api.handle("GET", "/api/profile/history/psnap_nope", {}, None, cookies=alice)
    assert status == 404 and payload["error"]["code"] == "PROFILE_SNAPSHOT_NOT_FOUND"
