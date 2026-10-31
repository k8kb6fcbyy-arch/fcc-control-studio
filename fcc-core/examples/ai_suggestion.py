"""AI 接入示例：演示「AI 只能当证人，不能当裁判」的两条红线。

    python examples/ai_suggestion.py

场景 A：只有 AI 说"温度异常" -> 不许干预（规则 A）
场景 B：AI 建议一个动作，即使 loop 配置成 auto -> 强制审批（规则 B）
场景 C：AI 建议 + 人工批准 -> 执行后照样只能 pending_verification（规则 C）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fcc import Client  # noqa: E402
from fcc.models import LoopConfig  # noqa: E402


def base_config(kind: str = "threshold", permission: str = "auto") -> dict:
    return {
        "name": f"AI 分离示例（{kind}）",
        "enabled": False,
        "goals": [{"goal_id": "g1", "target_variable": "temp", "direction": "approach",
                   "setpoint": {"kind": "value", "value": 50.0}, "tolerance": 1.0}],
        "observations": [{"metric_id": "temp", "source": "manual", "unit": "C",
                          "freshness_limit": 600, "missing_policy": "hold_last",
                          "outlier_policy": "flag"}],
        "controller": {"kind": kind, "params": {
            "proportional_gain": 0.5, "deadband": 0.0, "law": "tracking"}},
        "scheduler": {"mode": "manual"},
        "actuator": {"permission_mode": permission, "adapter": "manual"},
        "verification": {"delay_cycles": 1},
    }


def banner(text: str) -> None:
    print("\n" + "-" * 74)
    print(text)
    print("-" * 74)


def scenario_a() -> None:
    banner("场景 A · 只有 ai_inference 支持偏差 -> should_intervene 必须为 false")
    c = Client(":memory:")
    loop = c.create_loop(LoopConfig.model_validate(base_config()))
    c.submit_observation(loop, {
        "metric_id": "temp", "value": 88.0, "source_type": "ai_inference",
        "confidence": 0.93, "evidence": ["anomaly-model: 温度突升", "log: 3 次超阈"],
        "model": "glm-4-flash", "prompt_version": "v1",
    })
    res = c.run_cycle(loop)
    err = res.errors[0]
    print(f"观测来源        : ai_inference (confidence {res.observations['temp']['confidence']})")
    print(f"偏差            : {err['error']:+.2f}，超容差={err['out_of_tolerance']}")
    print(f"ai_only         : {err['ai_only']}")
    print(f"uncertainty     : {err['uncertainty']}")
    print(f"should_intervene: {res.should_intervene}    <- 必须是 False")
    print(f"human_review    : {res.human_review_required}")
    print(f"执行动作数      : {len(res.executed_actions)}")

    banner("场景 A' · 补一条 measured 观测后，同样的偏差就可以干预了")
    c.submit_observation(loop, {"metric_id": "temp", "value": 88.5,
                                "source_type": "measured"})
    res2 = c.run_cycle(loop)
    print(f"ai_only         : {res2.errors[0]['ai_only']}")
    print(f"should_intervene: {res2.should_intervene}    <- 现在可以为 True")
    print(f"执行动作数      : {len(res2.executed_actions)}")


def scenario_b() -> None:
    banner("场景 B · loop 配成 auto，AI 动作仍被强制降级为 require_approval")
    c = Client(":memory:")
    cfg = base_config(kind="ai_assisted", permission="auto")
    cfg["controller"]["params"]["static_suggestions"] = [{
        "value": 63.0, "confidence": 0.81,
        "evidence": ["forecast: 30 分钟后升温", "history: 上周同工况"],
        "risk_level": "medium", "reason": "提前降温", "goal_id": "g1",
        "rollback_method": "restore_previous_value",
    }]
    loop = c.create_loop(LoopConfig.model_validate(cfg))
    c.submit_observation(loop, {"metric_id": "temp", "value": 46.0,
                                "source_type": "measured"})
    res = c.run_cycle(loop)
    action = c.store.list_actions(loop)[0]
    print(f"配置权限模式    : auto")
    print(f"实际落库状态    : {action['status']}")
    print(f"required_approval: {action['required_approval']}")
    print(f"决策原因        : {action['decision_reason']}")
    print(f"guard 事件      :")
    for g in res.guard_events:
        print("   ", g)

    banner("场景 C · 人工批准后执行，状态只能是 pending_verification")
    out = c.approve_action(action["id"], "operator")
    print(f"批准后状态      : {out['action']['status']}")
    print(f"verification    : {out['action']['verification_status']}")
    print(f"验证报告        : {c.get_verification_report(loop)['counts']}")
    print("说明：此刻没有任何 verdict，improved 计数必然是 0。")


def main() -> None:
    scenario_a()
    scenario_b()
    banner("三条红线都由代码强制，不依赖配置或调用方自觉。")


if __name__ == "__main__":
    main()
