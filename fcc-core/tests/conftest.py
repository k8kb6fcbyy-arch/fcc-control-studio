"""公共夹具。测试只走公开接口（Client / HTTP），不 mock 核心逻辑。"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fcc import Client  # noqa: E402
from fcc.models import LoopConfig  # noqa: E402
from fcc.simplant import SimulatedPlant  # noqa: E402

BASE_CONFIG: dict = {
    "name": "test-loop",
    "enabled": False,
    "goals": [
        {
            "goal_id": "g1",
            "target_variable": "temp",
            "direction": "approach",
            "setpoint": {"kind": "value", "value": 50.0},
            "tolerance": 1.0,
        }
    ],
    "observations": [
        {
            "metric_id": "temp",
            "source": "manual",
            "unit": "C",
            "freshness_limit": 600.0,
            "missing_policy": "hold_last",
            "outlier_policy": "flag",
        }
    ],
    "controller": {
        "kind": "threshold",
        "params": {
            "proportional_gain": 0.5,
            "deadband": 0.0,
            "hysteresis": 0.0,
            "cooldown_cycles": 0,
            "law": "tracking",
        },
    },
    "scheduler": {"mode": "manual", "interval_seconds": 30},
    "actuator": {"permission_mode": "auto", "adapter": "manual", "max_step": 100.0},
    "verification": {"delay_cycles": 1, "noise_k": 2.0},
}


def cfg(**overrides) -> dict:
    """深拷贝一份基础配置，按需覆盖顶层键。"""
    data = copy.deepcopy(BASE_CONFIG)
    data.update(overrides)
    return data


def patch(data: dict, path: str, value) -> dict:
    """按 "a.b.c" 路径改配置，方便测试。"""
    node = data
    keys = path.split(".")
    for key in keys[:-1]:
        node = node[int(key)] if isinstance(node, list) else node[key]
    last = keys[-1]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value
    return data


@pytest.fixture()
def client(tmp_path) -> Client:
    c = Client(str(tmp_path / "fcc.db"))
    yield c
    c.close()


@pytest.fixture()
def plant() -> SimulatedPlant:
    return SimulatedPlant(tau=3.0, dead_time=1.0, noise_sigma=0.0, x0=0.0)


def new_loop(c: Client, data: dict | None = None) -> str:
    return c.create_loop(LoopConfig.model_validate(data or cfg()))


def observe(c: Client, loop_id: str, metric: str, value: float,
            source_type: str = "measured", **extra) -> dict:
    payload = {"metric_id": metric, "value": value, "source_type": source_type}
    payload.update(extra)
    return c.submit_observation(loop_id, payload)


def run(c: Client, loop_id: str):
    return c.run_cycle(loop_id)
