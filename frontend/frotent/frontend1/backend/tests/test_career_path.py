"""M1-4：路径引擎（``POST /api/v1/career-path/generate``）。

验收口径（M1-4）逐条钉住：
* 4 个职业都能生成；
* ``hard_checks`` 全 false（无环、id 有效、等级有效、阶段有效、gap 自洽、先修顺序正确）；
* 拓扑序满足所有 prerequisite 边（阶段不早于前置）；
* **同输入两次输出完全相同**（注入固定时钟，逐字节比对）；
* 未知职业 404。

另外把前端类型契约 `types/domain/career-path.ts` 的字段逐个钉一遍 ——
契约改了这里会红，避免后端悄悄少给字段、前端静默画空。
"""

from __future__ import annotations

import copy
import json

import pytest

from backend.career_path import STAGES
from backend.knowledge import GraphStore
from backend.server import CareerApi

FIXED_NOW = lambda: "2026-09-30T00:00:00Z"  # noqa: E731（测试固定时钟）


@pytest.fixture(scope="module")
def store() -> GraphStore:
    return GraphStore()


@pytest.fixture()
def api(store: GraphStore) -> CareerApi:
    return CareerApi(store=store)


def generate(api: CareerApi, body: dict):
    return api.handle("POST", "/api/v1/career-path/generate", {}, body)


# --------------------------------------------------------------------- 生成与硬校验

def test_all_occupations_generate(api: CareerApi) -> None:
    for occupation in api.store.nodes_of_kind("occupation"):
        code = occupation["id"].split(":")[-1]
        status, payload = generate(api, {"target_job": code, "weekly_hours": 10})
        assert status == 201, code
        data = payload["data"]
        assert data["occupation_id"] == code
        assert data["target_job"] == occupation["label"]
        assert data["weekly_hours"] == 10
        assert data["evaluation"]["path_valid"] is True
        assert [stage["stage"] for stage in data["path"]] == list(STAGES)


def test_hard_checks_all_false(api: CareerApi) -> None:
    for occupation in api.store.nodes_of_kind("occupation"):
        code = occupation["id"].split(":")[-1]
        data = generate(api, {"target_job": code})[1]["data"]
        checks = data["evaluation"]["hard_checks"]
        assert checks == {
            "prerequisite_cycle": False,
            "missing_skill_id": False,
            "invalid_level": False,
            "invalid_stage": False,
            "invalid_gap": False,
            "prerequisite_order": False,
        }, code


def test_stage_never_earlier_than_prerequisite(api: CareerApi) -> None:
    """拓扑序约束：任何技能所在阶段不得早于它的任一先修。"""
    order = {stage: index for index, stage in enumerate(STAGES)}
    for occupation in api.store.nodes_of_kind("occupation"):
        code = occupation["id"].split(":")[-1]
        data = generate(api, {"target_job": code})[1]["data"]
        by_id = {skill["skill_id"]: skill for stage in data["path"] for skill in stage["skills"]}
        for skill in by_id.values():
            for parent in skill["prerequisite_ids"]:
                if parent in by_id:
                    assert order[skill["default_stage"]] >= order[by_id[parent]["default_stage"]], (
                        code, skill["skill_id"], parent
                    )


def test_gap_is_self_consistent(api: CareerApi) -> None:
    body = {"target_job": "AI001", "current_skills": [{"skill_id": "SK090", "current_level": 3}]}
    data = generate(api, body)[1]["data"]
    for stage in data["path"]:
        for skill in stage["skills"]:
            assert skill["gap"] == max(0, skill["target_level"] - skill["current_level"])
            expected = "satisfied" if skill["gap"] == 0 else ("improve" if skill["current_level"] > 0 else None)
            if expected:
                assert skill["status"] == expected


def test_same_input_same_output(api: CareerApi, monkeypatch: pytest.MonkeyPatch) -> None:
    """同输入两次输出完全相同 —— 注入固定时钟后逐字节比对（含 generated_at）。"""
    monkeypatch.setattr("backend.career_path._now_iso", FIXED_NOW)
    body = {"target_job": "AI004", "weekly_hours": 12,
            "current_skills": [{"skill_id": "SK215", "current_level": 2}]}
    first = generate(api, copy.deepcopy(body))[1]["data"]
    second = generate(api, copy.deepcopy(body))[1]["data"]
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True)


def test_target_accepts_alias_and_name(api: CareerApi) -> None:
    by_name = generate(api, {"target_job": "嵌入式开发工程师"})[1]["data"]
    by_id = generate(api, {"target_job": "AI001"})[1]["data"]
    by_prefix = generate(api, {"target_job": "occupation:AI001"})[1]["data"]
    assert by_name["occupation_id"] == by_id["occupation_id"] == by_prefix["occupation_id"] == "AI001"


# --------------------------------------------------------------------- 错误语义

def test_unknown_occupation_is_404(api: CareerApi) -> None:
    status, payload = generate(api, {"target_job": "AI999"})
    assert status == 404
    assert payload["error"]["code"] == "OCCUPATION_NOT_FOUND"


def test_invalid_inputs_are_400(api: CareerApi) -> None:
    cases = [
        {},
        {"target_job": "   "},
        {"target_job": "AI001", "weekly_hours": 0},
        {"target_job": "AI001", "weekly_hours": "十"},
        {"target_job": "AI001", "current_skills": "SK090"},
        {"target_job": "AI001", "current_skills": [{"skill_id": "SK090", "current_level": 9}]},
        {"target_job": "AI001", "current_skills": [{"skill_id": "SK090", "current_level": "高"}]},
    ]
    for body in cases:
        status, payload = generate(api, body)
        assert status == 400, body
        assert payload["error"]["code"] == "INVALID_CAREER_PATH_INPUT", body


def test_unknown_skill_in_input_is_warned_not_dropped_silently(api: CareerApi) -> None:
    data = generate(api, {"target_job": "AI001", "current_skills": [{"skill_id": "SK-NOPE", "current_level": 3}]})[1]["data"]
    assert any("未匹配到图谱技能" in item for item in data["warnings"])


# --------------------------------------------------------------------- 契约字段

def test_contract_fields_present(api: CareerApi) -> None:
    """前端 `types/domain/career-path.ts` 要的字段一个都不能少。"""
    data = generate(api, {"target_job": "AI001", "weekly_hours": 10})[1]["data"]
    assert {
        "occupation_id", "target_job", "target_job_en", "profile_summary", "match_score",
        "match_type", "weekly_hours", "skill_gap_summary", "path", "evaluation",
        "generated_at", "rules_version", "metrics_version",
    } <= set(data)
    assert set(data["skill_gap_summary"]) == {
        "total_target_skills", "satisfied_count", "improve_count", "learning_count",
        "priority_learning_count",
    }
    evaluation = data["evaluation"]
    assert set(evaluation) == {
        "path_valid", "overall_score", "grade", "metrics", "hard_checks", "workload",
        "warnings", "suggestions",
    }
    assert set(evaluation["metrics"]) == {
        "prerequisite_reasonableness", "gap_coverage", "stage_alignment", "personalization",
        "task_skill_alignment", "executability",
    }
    assert set(evaluation["workload"]["stages"]) == set(STAGES)
    for stage in data["path"]:
        assert set(stage) == {
            "stage", "period", "goal", "skills", "tasks", "estimated_hours", "estimated_weeks",
            "satisfied_ratio", "compressed", "stage_skipped",
        }
        for skill in stage["skills"]:
            assert set(skill) == {
                "skill_id", "name_zh", "current_level", "target_level", "gap", "importance",
                "status", "prerequisite_ids", "priority_score", "prerequisite_depth",
                "default_stage", "prerequisite_only", "primary_learning",
            } | ({"stage_adjustment_reason"} if "stage_adjustment_reason" in skill else set())
        for task in stage["tasks"]:
            assert set(task) == {
                "task", "required_skill_ids", "tools", "deliverable", "evidence", "subtasks",
                "source_refs",
            }


def test_target_job_en_is_empty_not_invented(api: CareerApi) -> None:
    """导出里没有英文名：如实留空，不编一个像样的英文名出来。"""
    data = generate(api, {"target_job": "AI001"})[1]["data"]
    assert data["target_job_en"] == ""


def test_metrics_are_within_range(api: CareerApi) -> None:
    data = generate(api, {"target_job": "AI002"})[1]["data"]
    evaluation = data["evaluation"]
    assert 0 <= evaluation["overall_score"] <= 100
    for value in evaluation["metrics"].values():
        assert 0 <= value <= 100
    assert 0.0 <= data["match_score"] <= 1.0


def test_profile_skills_are_used_when_request_has_none(api: CareerApi) -> None:
    """M1-6：请求没给 current_skills 时用画像补，并在 warnings 里说明按 1 级计。"""
    api.handle("PUT", "/api/profile", {}, {"skills": "C 语言与内存模型", "source": "manual"})
    data = generate(api, {"target_job": "AI001", "weekly_hours": 10})[1]["data"]
    assert data["match_type"] == "profile_coverage"
    assert any("按入门（1 级）计入" in item for item in data["warnings"])
    by_id = {s["skill_id"]: s for stage in data["path"] for s in stage["skills"]}
    assert by_id["SK090"]["current_level"] == 1
    assert by_id["SK090"]["target_level"] > 1


def test_weekly_hours_changes_weeks_not_hours(api: CareerApi) -> None:
    slow = generate(api, {"target_job": "AI001", "weekly_hours": 5})[1]["data"]
    fast = generate(api, {"target_job": "AI001", "weekly_hours": 20})[1]["data"]
    slow_hours = [s["estimated_hours"] for s in slow["path"]]
    fast_hours = [s["estimated_hours"] for s in fast["path"]]
    assert slow_hours == fast_hours
    slow_weeks = [s["estimated_weeks"] for s in slow["path"]]
    fast_weeks = [s["estimated_weeks"] for s in fast["path"]]
    assert all(a >= b for a, b in zip(slow_weeks, fast_weeks))
    assert sum(slow_weeks) > sum(fast_weeks)
