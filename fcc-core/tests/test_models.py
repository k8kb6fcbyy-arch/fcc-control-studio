"""数据模型与三源分类的字段级硬约束（派单包 §2.1）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from conftest import cfg, patch
from fcc.models import LoopConfig, ObservationIn, Setpoint


def test_ai_observation_requires_confidence():
    with pytest.raises(ValidationError) as ei:
        ObservationIn.model_validate(
            {"metric_id": "temp", "value": 1.0, "source_type": "ai_inference",
             "evidence": ["log"]}
        )
    assert "confidence" in str(ei.value)


def test_ai_observation_requires_evidence():
    with pytest.raises(ValidationError) as ei:
        ObservationIn.model_validate(
            {"metric_id": "temp", "value": 1.0, "source_type": "ai_inference",
             "confidence": 0.8, "evidence": []}
        )
    assert "evidence" in str(ei.value)


def test_ai_observation_cannot_be_ground_truth():
    with pytest.raises(ValidationError) as ei:
        ObservationIn.model_validate(
            {"metric_id": "temp", "value": 1.0, "source_type": "ai_inference",
             "confidence": 0.8, "evidence": ["log"], "is_ground_truth": True}
        )
    assert "is_ground_truth" in str(ei.value)


def test_ai_observation_full_payload_is_ok():
    obs = ObservationIn.model_validate(
        {"metric_id": "temp", "value": 1.0, "source_type": "ai_inference",
         "confidence": 0.72, "evidence": ["trend-1", "alert-9"],
         "model": "glm-4-flash", "prompt_version": "v1"}
    )
    assert obs.is_ground_truth is False
    assert obs.confidence == 0.72


def test_measured_observation_is_ground_truth_by_default():
    obs = ObservationIn.model_validate(
        {"metric_id": "temp", "value": 1.0, "source_type": "measured"}
    )
    assert obs.is_ground_truth is True


def test_confidence_out_of_range_rejected():
    with pytest.raises(ValidationError):
        ObservationIn.model_validate(
            {"metric_id": "temp", "value": 1.0, "source_type": "ai_inference",
             "confidence": 1.5, "evidence": ["x"]}
        )


def test_goal_conflict_increase_vs_decrease():
    data = cfg()
    data["goals"][0]["direction"] = "increase"
    data["goals"].append({
        "goal_id": "g2", "target_variable": "temp", "direction": "decrease",
        "setpoint": {"kind": "value", "value": 48.0}, "tolerance": 1.0,
    })
    with pytest.raises(ValidationError) as ei:
        LoopConfig.model_validate(data)
    assert "目标冲突" in str(ei.value)


def test_goal_target_must_be_registered_observation():
    data = cfg(goals=[{
        "goal_id": "g1", "target_variable": "pressure", "direction": "approach",
        "setpoint": {"kind": "value", "value": 1.0}, "tolerance": 0.1,
    }])
    with pytest.raises(ValidationError) as ei:
        LoopConfig.model_validate(data)
    assert "未注册" in str(ei.value)


def test_range_setpoint_validation():
    Setpoint.model_validate({"kind": "range", "low": 1, "high": 2})
    with pytest.raises(ValidationError):
        Setpoint.model_validate({"kind": "range", "low": 3, "high": 2})
    with pytest.raises(ValidationError):
        Setpoint.model_validate({"kind": "value"})


def test_duplicate_goal_id_rejected():
    data = cfg()
    data["goals"].append(dict(data["goals"][0]))
    with pytest.raises(ValidationError):
        LoopConfig.model_validate(data)


def test_config_extra_field_rejected():
    data = cfg()
    data["unknown_field"] = 1
    with pytest.raises(ValidationError):
        LoopConfig.model_validate(data)


def test_patch_helper_works():
    data = patch(cfg(), "goals.0.tolerance", 3.0)
    assert data["goals"][0]["tolerance"] == 3.0
