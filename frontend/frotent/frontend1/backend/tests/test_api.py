"""最小后端的冒烟测试（pytest；只依赖标准库 + pytest）。

运行：``python -m pytest backend/tests -q``（等价于 npm run test:backend）。

这些测试把后端「读真实导出 → 按契约投影」这条链路钉死：
* 导出必须是第 11 步的真实交付物（status=pipeline-export，不是 contract-sample）；
* 契约字段名与计数必须与导出一致；
* 未实现的接口必须明确区分 501 与 404，不假装可用。
"""

from __future__ import annotations

import json
import threading
import urllib.request

import pytest

from backend.knowledge import GraphStore, code_of, default_export_path
from backend.server import CareerApi, create_server


@pytest.fixture(scope="module")
def store() -> GraphStore:
    return GraphStore()


@pytest.fixture()
def api(store: GraphStore) -> CareerApi:
    return CareerApi(store=store)


# --------------------------------------------------------------------- 数据层

def test_export_path_resolves_and_is_real_delivery(store: GraphStore) -> None:
    assert store.path.name == "career-graph.json"
    assert store.path.is_file(), default_export_path()
    meta = store.meta
    assert meta["status"] == "pipeline-export"
    assert meta["status"] != "contract-sample"
    assert meta["kbVersion"] == "2026.09.15"
    assert meta["graphVersion"] == "0.1.0"


def test_export_counts_match_meta(store: GraphStore) -> None:
    """:meta.counts 必须与实际数组长度一致；规模本身不写死（语料会扩容）。

    2026-09-27：语料从 494 段扩到 1757 段（新增 osgeo.cn pytest 中文三章 + 国家职业技能标准
    重组稿 S26）。原先这里写死 chunks == 494 / sources == 22，语料一扩容就变成假失败，
    所以改为「自洽断言」——检查 meta.counts 与真实数组长度、以及与逐来源登记数之和是否一致。
    """
    counts = store.counts
    assert counts["nodes"] == len(store.nodes_of_kind("occupation")) + len(store.nodes_of_kind("skill")) + len(
        store.nodes_of_kind("knowledge")
    ) + len(store.nodes_of_kind("task")) + len(store.nodes_of_kind("tool")) + len(store.nodes_of_kind("trend")) + len(
        store.nodes_of_kind("credential")
    ) + len(store.nodes_of_kind("domain"))
    assert counts["sources"] == len(store.raw.get("sources", []))
    assert counts["chunks"] == len(store.raw.get("chunks", []))
    assert counts["edges"] == len(store.raw.get("edges", []))
    # 逐来源登记数之和必须等于导出里的 chunk 总数（sources[].chunkCount × chunks[] 自洽）
    registered = sum(source.get("chunkCount") or 0 for source in store.raw.get("sources", []))
    assert registered == counts["chunks"], f"来源登记的 chunkCount 之和 {registered} ≠ 导出 chunks {counts['chunks']}"
    # 规模下限：语料至少要有来源与分块，避免空导出被当成合法
    assert counts["chunks"] > 0 and counts["sources"] > 0


def test_occupation_ids_are_four(store: GraphStore) -> None:
    ids = {code_of(n["id"]) for n in store.nodes_of_kind("occupation")}
    assert ids == {"AI001", "AI002", "AI003", "AI004"}


def test_required_skills_use_importance_order(store: GraphStore) -> None:
    rows = store.required_skills("occupation:AI001")
    assert len(rows) == 7
    importances = [r["importance"] for r in rows]
    assert importances == sorted(importances, reverse=True)
    assert rows[0]["skillId"] == "SK090"


# ------------------------------------------------------------------ 路由 / 契约

def test_health_exposes_real_source(api: CareerApi) -> None:
    status, payload = api.handle("GET", "/health", {}, None)
    assert status == 200
    assert payload["exportStatus"] == "pipeline-export"
    assert payload["kbVersion"] == "2026.09.15"
    assert payload["counts"]["nodes"] == 63
    assert payload["dataSource"].endswith("knowledge/exports/career-graph.json")


def test_envelope_shape(api: CareerApi) -> None:
    status, payload = api.handle("GET", "/api/v1/catalog/stats", {}, None)
    assert status == 200
    assert set(payload) == {"requestId", "data", "error"}
    assert payload["error"] is None
    assert payload["requestId"].startswith("req_")


def test_catalog_stats(api: CareerApi) -> None:
    _, payload = api.handle("GET", "/api/v1/catalog/stats", {}, None)
    data = payload["data"]
    assert data["occupations"] == 4
    assert data["skills"] == 25
    assert data["prerequisites"] == 24
    assert data["occupationSkills"] == 28
    assert data["stages"] == 9
    assert data["source"] in ("sqlite", "json-seed")


def test_occupations_list_and_keyword(api: CareerApi) -> None:
    _, payload = api.handle("GET", "/api/v1/occupations", {}, None)
    items = payload["data"]["items"]
    assert [i["occupationId"] for i in items] == ["AI001", "AI002", "AI003", "AI004"]
    assert all({"occupationId", "targetJob", "coreSkills", "skillCount", "stageCount"} <= set(i) for i in items)

    _, filtered = api.handle("GET", "/api/v1/occupations", {"keyword": ["视觉"]}, None)
    hits = filtered["data"]["items"]
    assert [i["occupationId"] for i in hits] == ["AI002"]


def test_occupation_detail(api: CareerApi) -> None:
    status, payload = api.handle("GET", "/api/v1/occupations/AI001", {}, None)
    assert status == 200
    detail = payload["data"]["occupation"]
    assert detail["occupationId"] == "AI001"
    assert detail["skillCount"] == 7
    assert len(detail["skills"]) == 7
    assert detail["skills"][0]["skillId"] == "SK090"
    assert detail["skills"][0]["categoryZh"] == "嵌入式与实时系统"
    assert detail["stages"][0]["stage"] == "stage1"
    assert detail["futureSignalZh"]
    assert detail["tasks"]


def test_occupation_detail_404(api: CareerApi) -> None:
    status, payload = api.handle("GET", "/api/v1/occupations/AI999", {}, None)
    assert status == 404
    assert payload["error"]["code"] == "OCCUPATION_NOT_FOUND"


def test_skills_list(api: CareerApi) -> None:
    _, payload = api.handle("GET", "/api/v1/skills", {}, None)
    items = payload["data"]["items"]
    assert len(items) == 25
    assert all({"skillId", "nameZh", "categoryZh", "prerequisites"} <= set(i) for i in items)


# ------------------------------------------------------------------ 岗位推荐

def test_recommendations_are_raw_not_enveloped(api: CareerApi) -> None:
    status, payload = api.handle("GET", "/api/career/recommendations", {}, None)
    assert status == 200
    assert "recommendations" in payload
    assert "data" not in payload
    rows = payload["recommendations"]
    assert len(rows) == 4
    required = {
        "occupation_id",
        "occupation_name",
        "match_score",
        "reason",
        "core_skills",
        "skill_gaps",
        "salary_range",
        "future_signal",
        "career_path",
    }
    assert all(required <= set(r) for r in rows)
    assert rows[0]["career_path"].startswith("/path?occupation=")
    assert all(isinstance(r["match_score"], int) and 0 <= r["match_score"] <= 100 for r in rows)


def test_recommendations_respect_profile(api: CareerApi) -> None:
    api.handle("PUT", "/api/profile", {}, {"identity": "在校生", "skills": "C语言、Python", "directions": "嵌入式开发"})
    _, payload = api.handle("GET", "/api/career/recommendations", {}, None)
    by_id = {r["occupation_id"]: r for r in payload["recommendations"]}
    ai001 = by_id["AI001"]
    assert ai001["match_score"] > 0
    assert "C 语言与内存模型" not in ai001["skill_gaps"]
    assert ai001["match_score"] == max(r["match_score"] for r in payload["recommendations"])


# -------------------------------------------------------------------- 画像流

def test_profile_flow(api: CareerApi) -> None:
    status, created = api.handle(
        "PUT",
        "/api/profile",
        {},
        {
            "identity": "应届生",
            "school": "浙江大学",
            "major": "自动化",
            "skills": "C语言,STM32",
            "directions": "嵌入式开发、边缘AI",
            "source": "manual",
        },
    )
    assert status == 200
    profile = created["data"]["profile"]
    assert profile["identity"] == "graduate"
    assert profile["status"] == "draft"
    assert profile["profileVersion"] == 1
    assert [s["name"] for s in profile["skills"]] == ["C语言", "STM32"]
    assert profile["candidateOccupationIds"] == ["AI001", "AI004"]

    _, fetched = api.handle("GET", "/api/profile", {}, None)
    assert fetched["data"]["profile"]["profileVersion"] == 1

    _, confirmed = api.handle("POST", "/api/profile/confirm", {}, None)
    assert confirmed["data"]["profile"]["status"] == "confirmed"


# -------------------------------------------------------------- HTTP 集成

def test_http_round_trip(store: GraphStore) -> None:
    httpd = create_server(host="127.0.0.1", port=0, store=store)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=10) as response:
            assert response.status == 200
            body = json.loads(response.read().decode("utf-8"))
        assert body["kbVersion"] == "2026.09.15"

        with urllib.request.urlopen(f"{base}/api/career/recommendations", timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
        assert len(body["recommendations"]) == 4
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_unknown_and_unimplemented_are_distinct(api: CareerApi) -> None:
    status, payload = api.handle("GET", "/api/career-matches/current", {}, None)
    assert status == 501
    assert payload["error"]["code"] == "NOT_IMPLEMENTED"

    status, payload = api.handle("GET", "/api/does-not-exist", {}, None)
    assert status == 404
    assert payload["error"]["code"] == "NOT_FOUND"


def test_career_path_generate_is_no_longer_501(api: CareerApi) -> None:
    """M1-4 之后这条路由必须真的生成路径，而不是继续回 501。"""
    status, payload = api.handle("POST", "/api/v1/career-path/generate", {}, {"target_job": "AI001"})
    assert status == 201
    assert payload["error"] is None
    assert payload["data"]["occupation_id"] == "AI001"
