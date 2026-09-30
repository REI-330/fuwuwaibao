"""记忆库测试（整合自队友实现的设计，按本项目约束重写后的验收）。

跑法：``python -m pytest backend/tests -q``。

这些断言钉住三件不能退让的事：
* **候选闸门**：`candidate` 永远不进入上下文与推荐（用户授权边界）；
* **确认不可被静默改写**：同来源重复同步不会覆盖用户已确认的事实；
* **删除即遗忘**：删记忆 → 触发器级联清空、`memoryHash` 变化、推荐回到基线。
"""

from __future__ import annotations

import json

import pytest

from backend.knowledge import GraphStore
from backend.memories import MemoryStore, build_triggers, derive_concepts
from backend.server import CareerApi, ProfileStore


@pytest.fixture(scope="module")
def store() -> GraphStore:
    return GraphStore()


@pytest.fixture()
def memories(store: GraphStore) -> MemoryStore:
    # 与 `CareerApi` 同样把图谱名词表传进去：触发器概念锚点才会取真实概念名
    instance = MemoryStore(":memory:", vocabulary=store.terms_in)
    yield instance
    instance.close()


@pytest.fixture()
def api(store: GraphStore, memories: MemoryStore) -> CareerApi:
    return CareerApi(store=store, profiles=ProfileStore(store), memories=memories)


def _confirm_profile(api: CareerApi, payload: dict) -> None:
    api.handle("PUT", "/api/profile", {}, payload)
    api.handle("POST", "/api/profile/confirm", {}, None)


# --------------------------------------------------------------------- 候选闸门

def test_manual_memory_defaults_to_confirmed(api: CareerApi) -> None:
    status, payload = api.handle(
        "POST", "/api/memories", {}, {"category": "skill", "content": "具备或正在学习：Python"}
    )
    assert status == 201
    item = payload["data"]["item"]
    assert item["status"] == "confirmed"
    assert item["sourceType"] == "manual"
    assert item["confirmedAt"]
    assert payload["data"]["item"]["triggersPending"] is False


def test_candidate_never_reaches_context_or_recommendations(api: CareerApi) -> None:
    _, created = api.handle(
        "POST",
        "/api/memories",
        {},
        {"category": "skill", "content": "具备或正在学习：C 语言与内存模型", "status": "candidate"},
    )
    memory_id = created["data"]["item"]["id"]

    _, listed = api.handle("GET", "/api/memories", {"status": ["candidate"]}, None)
    assert [item["id"] for item in listed["data"]["items"]] == [memory_id]
    assert listed["data"]["memoryHash"] == "", "候选记忆不该进入 confirmed 指纹"

    _, context = api.handle("GET", "/api/memories/context", {"query": ["C 语言"]}, None)
    assert context["data"]["count"] == 0
    assert context["data"]["memoryIds"] == []

    _, recommendations = api.handle("GET", "/api/career/recommendations", {}, None)
    assert all(row["confirmed_memory"] == [] for row in recommendations["recommendations"])
    assert recommendations["memory_hash"] == ""

    # 确认后才进入消费链路
    status, patched = api.handle("PATCH", f"/api/memories/{memory_id}", {}, {"status": "confirmed"})
    assert status == 200
    assert patched["data"]["item"]["status"] == "confirmed"
    assert patched["data"]["item"]["triggers"], "确认那一刻就该生成触发器"
    _, after = api.handle("GET", "/api/career/recommendations", {}, None)
    assert after["memory_hash"] != ""
    assert after["recommendations"][0]["confirmed_memory"], "确认后的记忆应作为输入增强出现"


# --------------------------------------------------------------------- 写入与幂等

def test_sync_requires_confirmed_profile(api: CareerApi) -> None:
    api.handle("PUT", "/api/profile", {}, {"identity": "在校生", "skills": "Python", "directions": "嵌入式开发"})
    _, before = api.handle("POST", "/api/memories/sync", {}, None)
    assert before["data"]["count"] == 0, "画像没确认，一条候选都不该写"

    api.handle("POST", "/api/profile/confirm", {}, None)
    _, synced = api.handle("POST", "/api/memories/sync", {}, None)
    assert synced["data"]["count"] >= 2
    assert all(item["status"] == "candidate" for item in synced["data"]["items"])
    assert all(item["sourceType"] == "profile" for item in synced["data"]["items"])
    contents = {item["content"] for item in synced["data"]["items"]}
    assert "具备或正在学习：Python" in contents
    assert "感兴趣的方向：嵌入式开发" in contents
    assert "目标职业：嵌入式开发工程师" in contents

    _, again = api.handle("POST", "/api/memories/sync", {}, None)
    assert [item["id"] for item in again["data"]["items"]] == [item["id"] for item in synced["data"]["items"]], "重复同步不产生新行"


def test_confirmed_memory_is_not_silently_overwritten(api: CareerApi) -> None:
    _confirm_profile(api, {"identity": "在校生", "skills": "Python", "directions": "嵌入式开发"})
    _, synced = api.handle("POST", "/api/memories/sync", {}, None)
    skill = next(item for item in synced["data"]["items"] if item["sourceId"] == "skill:0")
    api.handle("PATCH", f"/api/memories/{skill['id']}", {}, {"status": "confirmed"})

    # 用户改了画像里的技能：已确认的那条不能被改写
    api.handle("PUT", "/api/profile", {}, {"identity": "在校生", "skills": "Python、C语言", "directions": "嵌入式开发"})
    api.handle("POST", "/api/profile/confirm", {}, None)
    _, resynced = api.handle("POST", "/api/memories/sync", {}, None)
    same = next(item for item in resynced["data"]["items"] if item["id"] == skill["id"])
    assert same["status"] == "confirmed"
    assert same["content"] == skill["content"]


def test_invalid_inputs_are_rejected(api: CareerApi) -> None:
    status, payload = api.handle("POST", "/api/memories", {}, {"category": "不存在的类别", "content": "x"})
    assert status == 400
    assert payload["error"]["code"] == "INVALID_MEMORY"

    status, payload = api.handle("POST", "/api/memories", {}, {"category": "skill", "content": "   "})
    assert status == 400

    status, payload = api.handle("GET", "/api/memories", {"status": ["bogus"]}, None)
    assert status == 400
    assert payload["error"]["code"] == "INVALID_MEMORY_STATUS"

    status, payload = api.handle("PATCH", "/api/memories/memory_nope", {}, {"status": "confirmed"})
    assert status == 404
    assert payload["error"]["code"] == "MEMORY_NOT_FOUND"

    status, payload = api.handle("DELETE", "/api/memories/memory_nope", {}, None)
    assert status == 404


# --------------------------------------------------------------------- 召回

def test_context_has_persona_and_associative_recall(api: CareerApi) -> None:
    api.handle("POST", "/api/memories", {}, {"category": "career_target", "content": "目标职业：边缘 AI 工程师"})
    api.handle("POST", "/api/memories", {}, {"category": "skill", "content": "具备或正在学习：模型量化"})
    api.handle("POST", "/api/memories", {}, {"category": "preference", "content": "期望地点：上海"})

    _, context = api.handle("GET", "/api/memories/context", {"query": ["模型量化"]}, None)
    data = context["data"]
    assert data["count"] >= 1
    assert any(entry["memoryId"] in {item["id"] for item in data["persona"]} for entry in data["merged"])
    recalled = {entry["content"] for entry in data["recalled"]}
    assert "具备或正在学习：模型量化" in recalled
    assert "期望地点：上海" not in recalled, "无关记忆不该硬塞进来"
    assert data["memoryHash"] and ("【常驻】" in data["summaryText"] or "【本次想起】" in data["summaryText"])


def test_context_is_empty_when_no_confirmed_memory(api: CareerApi) -> None:
    _, context = api.handle("GET", "/api/memories/context", {"query": ["随便问问"]}, None)
    assert context["data"] == {
        "persona": [],
        "recalled": [],
        "merged": [],
        "memoryIds": [],
        "memoryHash": "",
        "summaryText": "",
        "count": 0,
    }


def test_irrelevant_query_returns_empty_recall(api: CareerApi) -> None:
    api.handle("POST", "/api/memories", {}, {"category": "goal", "content": "想做嵌入式软件工程师"})
    _, context = api.handle("GET", "/api/memories/context", {"query": ["量子计算芯片流片"]}, None)
    assert context["data"]["recalled"] == []


# --------------------------------------------------------------------- 触发器

def test_trigger_rules_are_deterministic_and_idempotent(api: CareerApi) -> None:
    _, created = api.handle("POST", "/api/memories", {}, {"category": "skill", "content": "具备或正在学习：模型量化"})
    memory_id = created["data"]["item"]["id"]
    _, listed = api.handle("GET", "/api/memories", {}, None)
    triggers = next(item for item in listed["data"]["items"] if item["id"] == memory_id)["triggers"]
    assert 1 <= len(triggers) <= 3
    for trigger in triggers:
        assert trigger["generatedBy"] == "rule-based"
        assert trigger["confidence"] == pytest.approx(0.6)
        assert len(trigger["activationPatterns"]) == 3
        assert trigger["bridge"]

    status, regenerated = api.handle("POST", f"/api/memories/{memory_id}/triggers", {}, None)
    assert status == 200
    assert {trigger["concept"] for trigger in regenerated["data"]["triggers"]} == {trigger["concept"] for trigger in triggers}
    _, relisted = api.handle("GET", "/api/memories", {}, None)
    after = next(item for item in relisted["data"]["items"] if item["id"] == memory_id)["triggers"]
    assert len(after) == len(triggers), "重复生成只能是替换，不能是追加"

    # 前缀「具备或正在学习：」被剥掉，概念锚点落在技能本身
    assert derive_concepts("具备或正在学习：模型量化", "skill")[0] == "模型量化"
    # 纯符号内容没有可派生的锚点，兜底用类别标签（保证「能被想起来」不依赖文本长度）
    assert derive_concepts("！！！", "custom") == ["自定义"]
    assert build_triggers("！！！", "custom", "2026-01-01T00:00:00Z")[0]["concept"] == "自定义"


def test_concepts_prefer_real_graph_terms(store: GraphStore, api: CareerApi) -> None:
    """概念锚点优先取图谱里已有的名词，而不是切出来的字片段。

    不这么说清楚就会出一个隐蔽的错：文本切分能把「轻量级推理引擎集成」切成
    「级推 / 量级」这种不成词的片段，界面上看着像乱码，召回也退化。
    """
    content = "具备或正在学习：轻量级推理引擎集成"
    # 不传词表：只能字面切分，锚点不成词（这就是要修的病）
    literal = derive_concepts(content, "skill")
    assert literal != ["轻量级推理引擎集成"]

    # 传图谱词表：锚点就是真实技能名（与导出里的 skill:SK120 label 一致）
    grounded = derive_concepts(content, "skill", store.terms_in)
    assert grounded[0] == "轻量级推理引擎集成"

    # 走完整链路：写入记忆后，触发器概念也是真实概念名
    _, created = api.handle("POST", "/api/memories", {}, {"category": "skill", "content": content})
    item = created["data"]["item"]
    _, listed = api.handle("GET", "/api/memories", {}, None)
    triggers = next(row for row in listed["data"]["items"] if row["id"] == item["id"])["triggers"]
    assert triggers[0]["concept"] == "轻量级推理引擎集成"
    # 句式里带上了这个概念 -> 联想召回能靠触发器命中（而不是只靠词面）
    assert any("轻量级推理引擎集成" in pattern for pattern in triggers[0]["activationPatterns"])
    _, recall = api.handle("GET", "/api/memories/context", {"query": ["我轻量级推理引擎集成那块进展怎么样"]}, None)
    assert recall["data"]["recalled"][0]["channel"].startswith("trigger:")


def test_terms_in_reads_existing_labels_and_aliases(store: GraphStore) -> None:
    """名词表只做查表：节点 label 与别名都认，且不新增任何图谱结论。"""
    assert store.terms_in("目标职业：边缘 AI 工程师") == ["边缘 AI 工程师"]
    assert "模型量化" in store.terms_in("我最近在补模型量化")  # 这是 skill:SK215 的别名
    assert store.terms_in("量子计算芯片流片工艺") == []


def test_trigger_generation_requires_confirmed_memory(api: CareerApi) -> None:
    _, created = api.handle(
        "POST", "/api/memories", {}, {"category": "goal", "content": "想转岗", "status": "candidate"}
    )
    memory_id = created["data"]["item"]["id"]
    status, payload = api.handle("POST", f"/api/memories/{memory_id}/triggers", {}, None)
    assert status == 400
    assert payload["error"]["code"] == "MEMORY_NOT_CONFIRMED"

    status, payload = api.handle("POST", "/api/memories/memory_nope/triggers", {}, None)
    assert status == 404
    assert payload["error"]["code"] == "MEMORY_NOT_FOUND"


# --------------------------------------------------------------------- 遗忘与推荐增强

def test_delete_is_forget(api: CareerApi) -> None:
    _, created = api.handle("POST", "/api/memories", {}, {"category": "skill", "content": "具备或正在学习：模型量化"})
    memory_id = created["data"]["item"]["id"]
    assert api.memories.triggers_for([memory_id]), "删除前应有触发器"
    assert api.memories.confirmed_hash("user_local") != ""

    status, payload = api.handle("DELETE", f"/api/memories/{memory_id}", {}, None)
    assert status == 200
    assert payload["data"]["deleted"] == memory_id
    assert payload["data"]["memoryHash"] == ""
    assert api.memories.triggers_for([memory_id]) == [], "触发器应随记忆级联清理"

    _, listed = api.handle("GET", "/api/memories", {}, None)
    assert listed["data"]["items"] == []
    _, context = api.handle("GET", "/api/memories/context", {"query": ["模型量化"]}, None)
    assert context["data"]["count"] == 0


def test_recommendation_is_augmented_by_confirmed_memory_only(api: CareerApi) -> None:
    api.handle("PUT", "/api/profile", {}, {"identity": "在校生", "skills": "Python"})
    api.handle("POST", "/api/profile/confirm", {}, None)

    _, baseline = api.handle("GET", "/api/career/recommendations", {}, None)
    before = {row["occupation_id"]: row["match_score"] for row in baseline["recommendations"]}
    assert baseline["memory_hash"] == ""

    # 候选：不该改变任何东西
    _, candidate = api.handle(
        "POST",
        "/api/memories",
        {},
        {"category": "skill", "content": "具备或正在学习：C 语言与内存模型", "status": "candidate"},
    )
    _, still = api.handle("GET", "/api/career/recommendations", {}, None)
    assert {row["occupation_id"]: row["match_score"] for row in still["recommendations"]} == before

    # 确认：AI001 的匹配度应上升，且逐条给出证据
    api.handle("PATCH", f"/api/memories/{candidate['data']['item']['id']}", {}, {"status": "confirmed"})
    _, augmented = api.handle("GET", "/api/career/recommendations", {}, None)
    after = {row["occupation_id"]: row["match_score"] for row in augmented["recommendations"]}
    assert after["AI001"] > before["AI001"]
    ai001 = next(row for row in augmented["recommendations"] if row["occupation_id"] == "AI001")
    assert ai001["confirmed_memory"] and ai001["confirmed_memory"][0]["usedFor"] == "skills"
    assert ai001["confirmed_memory"][0]["memoryId"] == candidate["data"]["item"]["id"]
    assert augmented["memory_hash"] != ""


def test_memory_does_not_duplicate_explicit_profile_skills(api: CareerApi) -> None:
    api.handle("PUT", "/api/profile", {}, {"identity": "在校生", "skills": "Python"})
    api.handle("POST", "/api/profile/confirm", {}, None)
    api.handle("POST", "/api/memories", {}, {"category": "skill", "content": "具备或正在学习：Python"})

    augmented, evidence = api.memories.augment_profile(api.profiles.get(), "user_local")
    names = [skill["name"] for skill in augmented["skills"]]
    assert names.count("Python") == 1, "已显式写在画像里的技能不该被记忆再补一遍"
    assert evidence == []


def test_skill_name_extraction_only_strips_known_prefixes() -> None:
    """抽技能名只认白名单前缀，不能「见到冒号就切」。

    反例很实在：`Python：异步编程` 被切成 `异步编程` 后，会变成一个图谱里不存在的技能名
    混进匹配算式 —— 用户看不出它是哪来的，也算不出为什么匹配度变了。
    """
    extract = MemoryStore.skill_name_from_memory
    assert extract("具备或正在学习：轻量级推理引擎集成") == "轻量级推理引擎集成"
    assert extract("掌握：Docker 基础") == "Docker 基础"
    assert extract("了解：pytest") == "pytest"
    assert extract("目标职业：边缘 AI 工程师") == "边缘 AI 工程师"
    assert extract("Python：异步编程") == "Python：异步编程"
    assert extract("  裸技能名  ") == "裸技能名"


def test_memory_hash_tracks_confirmed_set(api: CareerApi) -> None:
    user_id = "user_local"
    assert api.memories.confirmed_hash(user_id) == ""
    _, first = api.handle("POST", "/api/memories", {}, {"category": "goal", "content": "想做边缘 AI 方向"})
    hash_one = api.memories.confirmed_hash(user_id)
    assert hash_one
    api.handle("POST", "/api/memories", {}, {"category": "goal", "content": "想做嵌入式方向"})
    hash_two = api.memories.confirmed_hash(user_id)
    assert hash_two != hash_one
    api.handle("PATCH", f"/api/memories/{first['data']['item']['id']}", {}, {"content": "想做边缘 AI 部署"})
    assert api.memories.confirmed_hash(user_id) not in (hash_one, hash_two)


def test_memories_survive_new_api_instance(store: GraphStore, tmp_path) -> None:
    """M1-1 的最低要求：换一个 CareerApi 实例，记忆还在（不是进程内存）。"""
    db = tmp_path / "career-test.db"
    first_store = MemoryStore(str(db))
    first = CareerApi(store=store, profiles=ProfileStore(store), memories=first_store)
    first.handle("POST", "/api/memories", {}, {"category": "goal", "content": "目标：做模型量化"})
    first_store.close()

    second_store = MemoryStore(str(db))
    second = CareerApi(store=store, profiles=ProfileStore(store), memories=second_store)
    try:
        _, listed = second.handle("GET", "/api/memories", {}, None)
        assert [item["content"] for item in listed["data"]["items"]] == ["目标：做模型量化"]
        assert second.memories.triggers_for([listed["data"]["items"][0]["id"]]), "触发器也要落库"
    finally:
        second_store.close()


def test_http_memory_round_trip(store: GraphStore, memories: MemoryStore) -> None:
    """走真实 HTTP：POST → GET → DELETE，确认 PATCH/DELETE 方法真的挂上了。"""
    import threading
    import urllib.error
    import urllib.request

    from backend.server import create_server

    httpd = create_server(host="127.0.0.1", port=0, store=store, memories=memories)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        request = urllib.request.Request(
            f"{base}/api/memories",
            data=json.dumps({"category": "goal", "content": "目标：做模型量化"}).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            created = json.loads(response.read().decode("utf-8"))
        memory_id = created["data"]["item"]["id"]

        with urllib.request.urlopen(f"{base}/api/memories?status=confirmed", timeout=10) as response:
            listed = json.loads(response.read().decode("utf-8"))
        assert [item["id"] for item in listed["data"]["items"]] == [memory_id]

        delete = urllib.request.Request(f"{base}/api/memories/{memory_id}", method="DELETE")
        with urllib.request.urlopen(delete, timeout=10) as response:
            assert json.loads(response.read().decode("utf-8"))["data"]["deleted"] == memory_id

        with urllib.request.urlopen(f"{base}/api/memories", timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
        assert body["data"]["count"] == 0

        # 再删一次应该 404：不是「静默成功」
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(
                urllib.request.Request(f"{base}/api/memories/{memory_id}", method="DELETE"), timeout=10
            )
        assert error.value.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
