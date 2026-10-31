"""Regression for FCC 1.3 I/O friendly panel (independent of prior tests)."""
from fastapi.testclient import TestClient

from fcc.server import create_app


def config(*, setpoint=None, controller="monitor", mode="manual"):
    return {
        "name": "通用 I/O 端回路",
        "enabled": False,
        "metadata": {"ui": "easy-v1.3"},
        "goals": [{"goal_id": "g1", "target_variable": "metric_1",
                   "direction": "approach", "setpoint": setpoint or {"kind": "value", "value": 100},
                   "tolerance": 0 if setpoint and setpoint.get("kind") == "range" else 5}],
        "observations": [{"metric_id": "metric_1", "source": "manual", "unit": "",
                          "freshness_limit": 180}],
        "controller": {"kind": controller, "params": {"proportional_gain": .5} if controller=="threshold" else {}},
        "scheduler": {"mode": mode, "interval_seconds": 60},
        "actuator": {"permission_mode": "suggest_only", "adapter": "manual"},
    }


def test_easy_and_expert_routes():
    with TestClient(create_app(":memory:")) as api:
        for path in ["/", "/easy"]:
            r = api.get(path)
            assert r.status_code == 200
            assert "Input · 输入端" in r.text
            assert "Output · 输出端" in r.text
            assert "复制 I/O 配置与 AI 接入提示" in r.text
        assert "echarts" in api.get("/expert").text.lower()


def test_easy_io_cycle_and_repeated_observation():
    with TestClient(create_app(":memory:")) as api:
        lid=api.post("/api/loops",json={"config":config()}).json()["loop_id"]
        obs_url=f"/api/loops/{lid}/observations"
        cycle_url=f"/api/loops/{lid}/cycle"
        assert api.post(obs_url,json={"metric_id":"metric_1","value":85,"source_type":"human_input"}).status_code == 200
        d=api.post(cycle_url).json()
        assert d["errors"][0]["error"] == 15
        assert d["errors"][0]["out_of_tolerance"] is True
        assert d["proposed_actions"] == []
        assert api.post(obs_url,json={"metric_id":"metric_1","value":98,"source_type":"human_input"}).status_code==200
        d=api.post(cycle_url).json()
        assert d["errors"][0]["error"] == 2
        assert d["errors"][0]["out_of_tolerance"] is False
        assert len(api.get("/api/loops").json()["loops"]) == 1


def test_easy_io_range_and_ai_source_guard():
    with TestClient(create_app(":memory:")) as api:
        lid=api.post("/api/loops",json={"config":config(setpoint={"kind":"range","low":95,"high":105},controller="threshold")}).json()["loop_id"]
        url=f"/api/loops/{lid}/observations"
        assert api.post(url,json={"metric_id":"metric_1","value":91,"source_type":"ai_inference"}).status_code == 422
        obs={"metric_id":"metric_1","value":91,"source_type":"ai_inference","confidence":0.75,"evidence":["source is uncertain"]}
        assert api.post(url,json=obs).status_code==200
        d=api.post(f"/api/loops/{lid}/cycle").json()
        assert d["errors"][0]["out_of_tolerance"] is True
        assert d["proposed_actions"] == []


def test_easy_io_fixed_schedule_requires_explicit_enable():
    with TestClient(create_app(":memory:")) as api:
        lid=api.post("/api/loops",json={"config":config(mode="fixed")}).json()["loop_id"]
        s=api.get(f"/api/loops/{lid}").json()
        assert s["enabled"] is False
        assert api.post(f"/api/loops/{lid}/start").json()["enabled"] is True
        assert api.post(f"/api/loops/{lid}/pause").json()["enabled"] is False
