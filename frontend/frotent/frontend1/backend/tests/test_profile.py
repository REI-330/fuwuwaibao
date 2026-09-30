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
from backend.server import CareerApi, ProfileStore


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
