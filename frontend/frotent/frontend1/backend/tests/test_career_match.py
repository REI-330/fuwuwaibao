"""职业匹配（``/api/career-matches/*``）的测试（**全离线、不需要外部数据源**）。

这个模块曾经长期回 501，理由是「需要真实招聘数据源」。本文件的第一个作用就是证明**那归因是错的**：
契约里的字段（occupationId / skillGaps{targetLevel,importance} / tools / tasks）全部来自图谱与画像，
所以这四条路由可以在没有任何外网、任何凭证的情况下跑通。

钉住四件事：

* **契约字段逐个对齐** `types/contracts/career-match.ts`（多一个少一个都算失败）；
* **每个分数都能追到依据**：技能分能由图谱 requires 边手算复现，重要性刻度是边值的换算而非新估；
* **没有依据就不编**：没有方向 → 兴趣记 0 并进 needsValidation；没有经历 → 经历记 0 并进 needsValidation；
  图谱没有英文职业名 → 如实留空；
* **过期与授权语义**：未生成 → `CAREER_MATCH_NOT_FOUND`；画像/图谱变过 → `CAREER_MATCH_STALE`；
  画像太空 → `INSUFFICIENT_PROFILE`（不是给一份全 0 分的假结果）。
"""

from __future__ import annotations

import pytest

from backend.career_match import WEIGHTS, catalog_version, generate_run
from backend.knowledge import GraphStore, normalize
from backend.memories import MemoryStore
from backend.server import CareerApi, ProfileStore

# 与 types/contracts/career-match.ts 逐字对齐（键集合多一个少一个都要红）
RUN_KEYS = {
    "runId", "userId", "profileVersion", "catalogVersion",
    "confidence", "confidenceScore", "generatedAt", "items",
}
ITEM_KEYS = {
    "occupationId", "occupationName", "occupationNameEn", "shortName", "description",
    "rank", "matchScore", "matchLevel", "confidence", "confidenceScore",
    "scores", "matchedEvidence", "skillAdvantages", "skillGaps", "reasons",
    "needsValidation", "tools", "tasks",
}
GAP_KEYS = {"skillId", "name", "targetLevel", "importance"}
REASON_KEYS = {"type", "label", "detail", "source"}
SCORE_KEYS = {"interest", "skills", "experience", "entryFeasibility"}


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
    return CareerApi(store=store, profiles=ProfileStore(store, memories=memories), memories=memories)


def put_profile(api: CareerApi, **payload):
    status, body = api.handle("PUT", "/api/profile", {}, payload)
    assert status == 200, body
    return body["data"]["profile"]


def generate(api: CareerApi):
    return api.handle("POST", "/api/career-matches/generate", {}, None)


def top_item(run: dict) -> dict:
    return run["items"][0]


# --------------------------------------------------------------------- 契约与排序

def test_generate_ranks_every_graph_occupation_and_matches_contract(api: CareerApi) -> None:
    """4 个职业都要出现，且字段与契约逐一对齐。"""
    put_profile(api, skills="模型量化与部署、C 语言与内存模型", question="边缘 AI 工程师", directions="边缘 AI 工程师")
    status, payload = generate(api)
    assert status == 201, payload
    run = payload["data"]["run"]

    assert set(run) == RUN_KEYS, set(run) ^ RUN_KEYS
    assert len(run["items"]) == len(api.store.nodes_of_kind("occupation"))
    assert [item["rank"] for item in run["items"]] == list(range(1, len(run["items"]) + 1))
    # 排序必须真的按分数降序（不是照图谱顺序原样输出）
    scores = [item["matchScore"] for item in run["items"]]
    assert scores == sorted(scores, reverse=True)

    for item in run["items"]:
        assert set(item) == ITEM_KEYS, set(item) ^ ITEM_KEYS
        assert set(item["scores"]) == SCORE_KEYS
        assert 0 <= item["matchScore"] <= 100
        assert item["matchLevel"] in {"high", "medium", "exploratory"}
        assert item["confidence"] in {"low", "medium", "high"}
        for gap in item["skillGaps"]:
            assert set(gap) == GAP_KEYS
        for reason in item["reasons"]:
            assert set(reason) == REASON_KEYS
            assert reason["source"], "每条理由都必须写清出处，否则用户无从复核"


def test_english_occupation_name_is_empty_not_invented(api: CareerApi) -> None:
    """图谱导出里没有英文职业名 —— 契约字段必须留空，不臆造一个英文名。"""
    put_profile(api, skills="模型量化与部署", question="边缘 AI 工程师")
    _, payload = generate(api)
    for item in payload["data"]["run"]["items"]:
        assert item["occupationNameEn"] == ""
        assert item["occupationName"]  # 中文名必须有
        assert item["shortName"]


def test_skill_score_is_recomputable_from_requires_edges(api: CareerApi) -> None:
    """技能分必须能由图谱 requires 边的 importance 手算复现 —— 不是拍出来的数。"""
    profile = put_profile(api, skills="模型量化与部署、C 语言与内存模型", question="边缘 AI 工程师")
    _, payload = generate(api)
    run = payload["data"]["run"]

    for item in run["items"]:
        node = api.store.node(f"occupation:{item['occupationId']}")
        required = api.store.required_skills(node["id"])
        total = sum(float(r["importance"] or 0) for r in required)
        assert total > 0
        covered = 0.0
        # 与实现同口径：owned 必须先 normalize（图谱的 owns_skill 就是按 normalize 比对的）
        owned = {normalize(s["name"]) for s in profile["skills"]}
        for row in required:
            skill_node = api.store.node(row["skillNodeId"]) or {}
            if api.store.owns_skill(skill_node, owned):
                covered += float(row["importance"] or 0)
        assert item["scores"]["skills"] == round(100 * covered / total)

        # 缺口里的 importance 是「边值的 1..5 刻度换算」，不是重新估的数
        by_id = {r["skillId"]: float(r["importance"] or 0) for r in required}
        for gap in item["skillGaps"]:
            expected = max(1, min(5, int(round(by_id[gap["skillId"]] * 5))))
            assert gap["importance"] == expected
            assert 1 <= gap["targetLevel"] <= 5


def test_match_score_is_the_documented_weighted_blend(api: CareerApi) -> None:
    """总分 = 四维加权；权重是写在常量里的产品假设，替换权重不该影响图谱事实。"""
    put_profile(api, skills="模型量化与部署", question="边缘 AI 工程师")
    _, payload = generate(api)
    for item in payload["data"]["run"]["items"]:
        expected = round(sum(item["scores"][key] * WEIGHTS[key] for key in WEIGHTS))
        assert item["matchScore"] == expected
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


# --------------------------------------------------------------------- 没有依据就不编

def test_missing_direction_is_scored_zero_and_flagged(api: CareerApi) -> None:
    """只填了技能、没写方向 → 兴趣维度记 0 并进 needsValidation，不猜用户想干什么。"""
    put_profile(api, skills="模型量化与部署")
    _, payload = generate(api)
    for item in payload["data"]["run"]["items"]:
        assert item["scores"]["interest"] == 0
        assert any("方向" in question for question in item["needsValidation"])


def test_missing_experience_is_scored_zero_and_flagged(api: CareerApi) -> None:
    put_profile(api, skills="模型量化与部署", question="边缘 AI 工程师")
    _, payload = generate(api)
    for item in payload["data"]["run"]["items"]:
        assert item["scores"]["experience"] == 0
        assert any("经历" in question for question in item["needsValidation"])


def test_empty_profile_is_refused_instead_of_returning_zeros(api: CareerApi) -> None:
    """画像太空时明确拒答 —— 返回一份全是 0 分的「匹配结果」等于假装算过了。"""
    status, payload = api.handle("POST", "/api/career-matches/generate", {}, None)
    assert status == 409
    assert payload["error"]["code"] == "INSUFFICIENT_PROFILE"
    assert payload["error"]["message"]


def test_generate_never_invents_target_skills(api: CareerApi) -> None:
    """用户写一个图谱里不存在的技能 → 不进 skillAdvantages，也不算命中。"""
    put_profile(api, skills="炖菜、模型量化与部署", question="边缘 AI 工程师")
    _, payload = generate(api)
    run = payload["data"]["run"]
    all_advantages = [name for item in run["items"] for name in item["skillAdvantages"]]
    assert "炖菜" not in all_advantages
    assert "模型量化与部署" in all_advantages


# --------------------------------------------------------------------- 取值 / 过期 / 选择

def test_current_requires_generation_first(api: CareerApi) -> None:
    status, payload = api.handle("GET", "/api/career-matches/current", {}, None)
    assert status == 404
    assert payload["error"]["code"] == "CAREER_MATCH_NOT_FOUND"


def test_current_returns_the_generated_run(api: CareerApi) -> None:
    put_profile(api, skills="模型量化与部署", question="边缘 AI 工程师")
    _, created = generate(api)
    status, payload = api.handle("GET", "/api/career-matches/current", {}, None)
    assert status == 200
    assert payload["data"]["run"]["runId"] == created["data"]["run"]["runId"]


def test_profile_change_makes_the_run_stale(api: CareerApi) -> None:
    """画像改了就判过期，让前端重算 —— 不拿旧结果继续显示。"""
    put_profile(api, skills="模型量化与部署", question="边缘 AI 工程师")
    generate(api)
    put_profile(api, skills="模型量化与部署、C 语言与内存模型", question="边缘 AI 工程师")
    status, payload = api.handle("GET", "/api/career-matches/current", {}, None)
    assert status == 409
    assert payload["error"]["code"] == "CAREER_MATCH_STALE"


def test_catalog_version_participates_in_staleness(api: CareerApi) -> None:
    put_profile(api, skills="模型量化与部署", question="边缘 AI 工程师")
    _, payload = generate(api)
    run = payload["data"]["run"]
    assert run["catalogVersion"] == catalog_version(api.store)


def test_run_survives_a_new_api_instance(store: GraphStore) -> None:
    """落库了就要能在新建实例后读到（与 M1-1 画像同口径，不是进程内内存）。"""
    memories = MemoryStore(":memory:", vocabulary=store.terms_in)
    try:
        first = CareerApi(store=store, profiles=ProfileStore(store, memories=memories), memories=memories)
        first.handle("PUT", "/api/profile", {}, {"skills": "模型量化与部署", "question": "边缘 AI 工程师"})
        created = first.handle("POST", "/api/career-matches/generate", {}, None)[1]["data"]["run"]

        second = CareerApi(store=store, profiles=ProfileStore(store, memories=memories), memories=memories)
        status, payload = second.handle("GET", "/api/career-matches/current", {}, None)
        assert status == 200
        assert payload["data"]["run"]["runId"] == created["runId"]
    finally:
        memories.close()


def test_select_persists_target_and_rejects_unknown(api: CareerApi) -> None:
    put_profile(api, skills="模型量化与部署", question="边缘 AI 工程师")
    generate(api)

    status, payload = api.handle("POST", "/api/career-matches/select", {}, {"occupationId": "AI004"})
    assert status == 200, payload
    target = payload["data"]["target"]
    assert target["occupationId"] == "AI004"
    assert target["selectedAt"]
    assert target["matchRunId"].startswith("match_")

    assert api.handle("POST", "/api/career-matches/select", {}, {"occupationId": "AI999"})[0] == 404
    assert api.handle("POST", "/api/career-matches/select", {}, {})[0] == 400
    assert api.handle("POST", "/api/career-matches/select", {}, None)[0] == 400


def test_runs_are_isolated_per_user(store: GraphStore, memories: MemoryStore) -> None:
    """两个会话各自匹配，互不可见（与画像/记忆同一条授权纪律）。"""
    api = CareerApi(store=store, profiles=ProfileStore(store, memories=memories), memories=memories)
    token_a = {"career_session": "user_aaaaaaaaaaaa"}
    token_b = {"career_session": "user_bbbbbbbbbbbb"}

    api.handle("PUT", "/api/profile", {}, {"skills": "模型量化与部署", "question": "边缘 AI 工程师"}, cookies=token_a)
    api.handle("POST", "/api/career-matches/generate", {}, None, cookies=token_a)

    assert api.handle("GET", "/api/career-matches/current", {}, None, cookies=token_a)[0] == 200
    assert api.handle("GET", "/api/career-matches/current", {}, None, cookies=token_b)[0] == 404


# --------------------------------------------------------------------- 模块级入口

def test_generate_run_is_pure_over_store_and_profile(store: GraphStore) -> None:
    """直接调模块函数也要能跑（CLI/脚本复用），且同一输入给出同一排序。"""
    profile = {"userId": "user_local", "status": "confirmed", "profileVersion": 3,
               "skills": [{"name": "模型量化与部署", "level": "unknown"}],
               "currentGoal": "边缘 AI 工程师", "interests": [], "experiences": []}
    first = generate_run(store, profile, "user_local", generated_at="2026-09-30T00:00:00Z")
    second = generate_run(store, profile, "user_local", generated_at="2026-09-30T00:00:00Z")
    assert [i["occupationId"] for i in first["items"]] == [i["occupationId"] for i in second["items"]]
    assert [i["matchScore"] for i in first["items"]] == [i["matchScore"] for i in second["items"]]
