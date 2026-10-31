"""纯 CLI 接入示例：不启 HTTP、不开浏览器，直接在终端里跑闭环。

    python examples/cli_demo.py

演示：观测由外部程序写入（这里用一个伪传感器函数生成），
控制动作写成 shell 命令回显 —— 说明 Actuator 如何对接任意"外部世界"。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fcc import Client  # noqa: E402
from fcc.models import LoopConfig  # noqa: E402

CONFIG = {
    "name": "CLI 示例：CPU 占用率控制",
    "enabled": False,
    "goals": [{
        "goal_id": "g_cpu",
        "target_variable": "cpu_pct",
        "direction": "approach",
        "setpoint": {"kind": "range", "low": 45.0, "high": 60.0},
        "tolerance": 0.0,
        "priority": 3,
    }],
    "observations": [{
        "metric_id": "cpu_pct", "source": "manual", "unit": "%",
        "freshness_limit": 120, "missing_policy": "hold_last",
        "outlier_policy": "flag", "valid_range": [0, 100],
    }],
    "controller": {"kind": "rule", "params": {"rules": [
        {"id": "scale_out",
         "if": {"metric": "cpu_pct", "op": "gt", "value": 60},
         "action": {"value": 3, "risk": "medium", "goal_id": "g_cpu"},
         "reason": "CPU 高于上限 -> 加副本"},
        {"id": "scale_in",
         "if": {"metric": "cpu_pct", "op": "lt", "value": 45},
         "action": {"value": 1, "risk": "medium", "goal_id": "g_cpu"},
         "reason": "CPU 低于下限 -> 减副本"},
    ]}},
    "scheduler": {"mode": "manual"},
    "actuator": {"permission_mode": "require_approval", "adapter": "replicas",
                 "action_range": [1, 5]},
    "verification": {"delay_cycles": 1, "noise_k": 2.0},
}


class FakeCluster:
    """模拟"副本数 -> CPU"的对象，同时充当执行适配器。"""

    def __init__(self) -> None:
        self.replicas = 1
        self.commands: list[str] = []

    def cpu(self, tick: int) -> float:
        # 副本越多 CPU 越低；叠一个正弦负载
        base = 90.0 / self.replicas
        load = 12.0 * math.sin(tick / 3.0)
        return max(1.0, min(99.0, base + load))

    def apply(self, action: dict) -> dict:
        target = int(action["params"]["value"])
        previous = self.replicas
        self.replicas = target
        cmd = f"kubectl scale deploy/api --replicas={target}"
        self.commands.append(cmd)
        return {"ok": True, "previous_value": previous, "applied_value": target,
                "detail": cmd}

    def state(self) -> dict:
        return {"current_value": self.replicas, "commands": self.commands[-1:]}

    def rollback(self, action: dict, result: dict) -> dict:
        prev = int(result.get("previous_value", 1))
        self.replicas = prev
        return {"ok": True, "detail": f"回滚到 {prev} 个副本"}


def main() -> None:
    client = Client(":memory:")
    cluster = FakeCluster()
    loop = client.create_loop(LoopConfig.model_validate(CONFIG))
    client.register_actuator("replicas", cluster)

    print(f"loop_id = {loop}")
    print(f"{'tick':>4} {'CPU%':>6} {'副本':>4} {'偏差':>7} {'状态':>20} {'验证':>12}")
    for tick in range(1, 13):
        cpu = cluster.cpu(tick)
        client.submit_observation(loop, {"metric_id": "cpu_pct", "value": cpu,
                                         "source_type": "measured"})
        res = client.run_cycle(loop)
        err = res.errors[0]
        action = res.executed_actions[0] if res.executed_actions else None
        verdict = ",".join(v["verdict"] for v in res.verifications) or "-"
        print(f"{tick:>4} {cpu:>6.1f} {cluster.replicas:>4} "
              f"{(err['error'] or 0):>+7.2f} "
              f"{(action['status'] if action else '-'):>20} {verdict:>12}")

        # 命令行里"人工审批"：这里演示自动批准，真实场景由人决定
        for pending in client.store.actions_awaiting_approval(loop):
            out = client.approve_action(pending["id"], "cli-operator")
            print(f"     批准动作 #{pending['id']} "
                  f"({pending['origin_detail']}) -> {out['action']['status']}")

    print("\n执行过的命令：")
    for cmd in cluster.commands:
        print("  $", cmd)
    print("\n验证统计：", client.get_verification_report(loop)["counts"])
    print("学习统计：", client.engine.get_state(loop)["strategy_stats"]["strategy_versions"])


if __name__ == "__main__":
    main()
