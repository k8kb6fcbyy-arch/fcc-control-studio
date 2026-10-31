"""最小的端到端冒烟：建 loop -> 观测 -> 偏差 -> 建议 -> 审批 -> 执行 -> 验证。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fcc import Client  # noqa: E402
from fcc.models import LoopConfig  # noqa: E402
from fcc.simplant import SimulatedPlant  # noqa: E402

CFG = {
    "name": "smoke",
    "enabled": False,
    "goals": [{
        "goal_id": "g_temp",
        "target_variable": "temp",
        "direction": "approach",
        "setpoint": {"kind": "value", "value": 50.0},
        "tolerance": 1.0,
    }],
    "observations": [{
        "metric_id": "temp",
        "source": "manual",
        "unit": "C",
        "freshness_limit": 600,
        "missing_policy": "hold_last",
        "outlier_policy": "flag",
    }],
    "controller": {"kind": "threshold", "params": {"proportional_gain": 0.5,
                                                   "deadband": 0.5, "cooldown_cycles": 0}},
    "scheduler": {"mode": "manual"},
    "actuator": {"permission_mode": "auto", "adapter": "manual"},
    "verification": {"delay_cycles": 1, "noise_k": 2.0},
}


def main() -> None:
    c = Client(":memory:")
    plant = SimulatedPlant(tau=6.0, dead_time=1.0, noise_sigma=0.0, x0=10.0)
    c.register_actuator("plant", plant)

    cfg = LoopConfig.model_validate(CFG)
    cfg.actuator.adapter = "plant"
    loop = c.create_loop(cfg)

    for step in range(1, 13):
        plant.advance()
        obs = plant.read()
        c.submit_observation(loop, {"metric_id": "temp", "value": obs["value"],
                                    "source_type": "measured"})
        res = c.run_cycle(loop)
        err = res.errors[0]
        flag = "干涉" if res.executed_actions else "  - "
        print(f"cycle {step:02d} y={obs['value']:7.3f} sp=50.0 e={err['error']:+7.3f} "
              f"{flag} status={res.verification_status} "
              f"actions={[a['status'] for a in res.executed_actions]}")

    print("verification report:", c.get_verification_report(loop)["counts"])


if __name__ == "__main__":
    main()
