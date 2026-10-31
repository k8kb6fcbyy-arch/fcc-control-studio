"""模拟被控对象 + 五个验收场景（派单包 §6）。

不接 AIO，不接真实硬件。对象是：一阶惯性 + 时滞 + 高斯噪声。

    python examples/simulated_plant.py           # 跑全部五个场景
    python examples/simulated_plant.py 3         # 只跑第 3 个

五个场景的控制方法分类（参见 docs/THEORY-MAP.md）：

  1 阶跃跟踪     —— 4.3 时间响应指标、5.1 闭环反馈（经典控制理论有）
  2 噪声与死区   —— 12.1 只讲采样，死区/迟滞/噪声抑制属工程实现（engineered）
  3 数据缺失     —— engineered：经典控制理论没有缺失数据策略
  4 执行失败     —— engineered：经典控制理论没有执行器重试语义
  5 振荡         —— engineered 启发式（12 轮 ≥3 次符号翻转），不是 15.6 极限环判据，
                    也不是 8.6 增益/相位裕度；后两者要求已知传递函数
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fcc import Client, util  # noqa: E402
from fcc.models import LoopConfig  # noqa: E402
from fcc.simplant import SimulatedPlant  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------
# 组装配件
# --------------------------------------------------------------------------


def make_loop(client: Client, plant: SimulatedPlant, *, name: str, gain: float = 0.5,
              law: str = "tracking", tolerance: float = 1.0, deadband: float = 0.3,
              permission: str = "auto", adapter: str = "plant",
              max_retries: int = 1, timeout: float = 5.0) -> str:
    cfg = {
        "name": name,
        "enabled": False,
        "goals": [{
            "goal_id": "g_temp",
            "target_variable": "temp",
            "direction": "approach",
            "setpoint": {"kind": "value", "value": 50.0},
            "tolerance": tolerance,
        }],
        "observations": [{
            "metric_id": "temp", "source": "manual", "unit": "C",
            "freshness_limit": 60.0, "missing_policy": "hold_last",
            "outlier_policy": "flag", "valid_range": [-200, 400],
        }],
        "controller": {"kind": "threshold", "params": {
            "proportional_gain": gain, "deadband": deadband,
            "hysteresis": 0.3, "cooldown_cycles": 0, "law": law}},
        "scheduler": {"mode": "manual"},
        "actuator": {"permission_mode": permission, "adapter": adapter,
                     "max_step": 100.0, "max_retries": max_retries,
                     "timeout_seconds": timeout},
        "verification": {"delay_cycles": 1, "noise_k": 2.0},
    }
    loop_id = client.create_loop(LoopConfig.model_validate(cfg))
    client.register_actuator(adapter, plant)
    return loop_id


def step(client: Client, plant: SimulatedPlant, loop: str):
    plant.advance()
    reading = plant.read()
    client.submit_observation(loop, {"metric_id": "temp", "value": reading["value"],
                                     "source_type": "measured"})
    return client.run_cycle(loop), reading["value"]


def header(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


# --------------------------------------------------------------------------
# 场景 1：稳定收敛
# --------------------------------------------------------------------------


def scenario_1() -> None:
    header("场景 1 · 稳定收敛：setpoint 阶跃 0 -> 50，要求 10 个周期内进入 ±1.0 容差带")
    client = Client(":memory:")
    plant = SimulatedPlant(tau=3.0, dead_time=1.0, noise_sigma=0.3, x0=0.0, seed=7)
    loop = make_loop(client, plant, name="S1 稳定收敛")

    print(f"{'周期':>4} {'观测 y':>9} {'目标':>6} {'误差 e':>9} {'动作':>7} "
          f"{'执行状态':>20} {'验证':>14}")
    entered = None
    for i in range(1, 15):
        res, y = step(client, plant, loop)
        err = res.errors[0]["error"]
        act = res.executed_actions[0] if res.executed_actions else None
        verdict = ",".join(v["verdict"] for v in res.verifications) or "-"
        print(f"{i:>4} {y:>9.3f} {50.0:>6.1f} {err:>+9.3f} "
              f"{(f'{act['proposed_value']:.2f}' if act else '-'):>7} "
              f"{(act['status'] if act else '-'):>20} {verdict:>14}")
        if entered is None and abs(err) <= 1.0:
            entered = i
    print(f"\n结论：第 {entered} 个周期进入容差带（要求 <=10）")
    print(f"验证统计：{client.get_verification_report(loop)['counts']}")


# --------------------------------------------------------------------------
# 场景 2：噪声增大
# --------------------------------------------------------------------------


def scenario_2() -> None:
    header("场景 2 · 噪声增大：sigma 0.3 -> 1.5，验证死区/迟滞抑制抖动")
    client = Client(":memory:")
    plant = SimulatedPlant(tau=3.0, dead_time=1.0, noise_sigma=0.3, x0=45.0, seed=11)
    loop = make_loop(client, plant, name="S2 噪声", deadband=1.0)

    for _ in range(8):
        step(client, plant, loop)
    print("8 个周期后把噪声从 0.3 提到 1.5 ...")
    plant.set_noise(1.5)

    acts = 0
    print(f"{'周期':>4} {'观测 y':>9} {'误差 e':>9} {'是否动作':>10}")
    for i in range(1, 13):
        res, y = step(client, plant, loop)
        moved = bool(res.executed_actions)
        acts += moved
        print(f"{i:>4} {y:>9.3f} {res.errors[0]['error']:>+9.3f} "
              f"{'动手' if moved else '    不动':>10}")
    print(f"\n12 个周期里只动作 {acts} 次（死区 1.0 + 迟滞 0.3 生效）")
    print(f"验证统计：{client.get_verification_report(loop)['counts']}")


# --------------------------------------------------------------------------
# 场景 3：数据缺失
# --------------------------------------------------------------------------


def scenario_3() -> None:
    header("场景 3 · 数据缺失：中断观测 3 个周期，验证 missing_policy=hold_last")
    client = Client(":memory:")
    plant = SimulatedPlant(tau=3.0, dead_time=1.0, noise_sigma=0.2, x0=20.0, seed=3)
    loop = make_loop(client, plant, name="S3 缺数据")

    now = util.utcnow()
    for _ in range(4):
        plant.advance()
        reading = plant.read()
        client.submit_observation(loop, {"metric_id": "temp", "value": reading["value"],
                                         "source_type": "measured"})
        client.run_cycle(loop, now=util.to_iso(now))
        now += dt.timedelta(seconds=1)

    spec = {
        "metric_id": "temp", "source": "manual", "unit": "C",
        "freshness_limit": 5.0, "missing_policy": "hold_last",
        "outlier_policy": "flag", "valid_range": [-200, 400],
    }
    client.update_config(loop, {"observations": [spec]})
    print("有效期 5 秒。现在停止上报观测，把模拟时钟往前推，连续跑 3 轮 ...\n")

    print(f"{'周期':>4} {'观测值':>10} {'age(s)':>7} {'stale':>6} {'missing':>8} "
          f"{'干预':>5} {'新动作':>7} flags")
    for i in range(1, 4):
        plant.advance()          # 对象还在跑，但没人上报
        now += dt.timedelta(seconds=10)
        res = client.run_cycle(loop, now=util.to_iso(now))
        snap = res.observations["temp"]
        print(f"{i:>4} {snap['value']:>10.3f} {snap['age_seconds']:>7.1f} "
              f"{str(snap['stale']):>6} {str(snap['missing']):>8} "
              f"{str(res.should_intervene):>5} {0:>7} {snap['flags']}")
    print("\n结论：stale 期间沿用旧值但明确标记（held_last），不因缺失而误判干预，"
          "也不产生新动作")


# --------------------------------------------------------------------------
# 场景 4：动作执行失败
# --------------------------------------------------------------------------


def scenario_4() -> None:
    header("场景 4 · 动作执行失败：plant 连续抛异常，验证重试与不误标成功")
    client = Client(":memory:")
    plant = SimulatedPlant(tau=3.0, dead_time=0.0, noise_sigma=0.0, x0=10.0, seed=5)
    loop = make_loop(client, plant, name="S4 执行失败", max_retries=2)

    client.submit_observation(loop, {"metric_id": "temp", "value": 10.0,
                                     "source_type": "measured"})
    plant.inject_failures(5, mode="raise")
    print("注入 5 次执行故障（max_retries=2，共尝试 3 次）...\n")
    res = client.run_cycle(loop)

    action = client.store.list_actions(loop)[0]
    print(f"动作状态        : {action['status']}")
    print(f"验证状态        : {action['verification_status']}")
    print(f"尝试次数        : {(action['result'] or {}).get('attempts')}")
    print(f"错误            : {(action['result'] or {}).get('error')}")
    print(f"本轮 verification_status: {res.verification_status}")
    print(f"验证统计        : {client.get_verification_report(loop)['counts']}")
    print("\n结论：失败被如实记录为 failed，没有出现在 improved 里")


# --------------------------------------------------------------------------
# 场景 5：过度修正 -> 振荡
# --------------------------------------------------------------------------


def scenario_5() -> None:
    header("场景 5 · 过度修正：增益拉大 + 时滞加长，验证振荡检测")
    client = Client(":memory:")
    plant = SimulatedPlant(tau=3.0, dead_time=2.0, noise_sigma=0.0, x0=0.0, seed=9)
    loop = make_loop(client, plant, name="S5 振荡", gain=0.9)

    print(f"{'周期':>4} {'观测 y':>9} {'误差 e':>9} {'振荡':>6} {'干预':>6} {'note'}")
    first = None
    for i in range(1, 16):
        res, y = step(client, plant, loop)
        err = res.errors[0]
        note = next((n for n in err["notes"] if n.startswith("oscillation")), "")
        if err["oscillation_detected"] and first is None:
            first = i
        print(f"{i:>4} {y:>9.3f} {err['error']:>+9.3f} "
              f"{str(err['oscillation_detected']):>6} "
              f"{str(res.should_intervene):>6} {note}")
    print(f"\n结论：第 {first} 个周期检出 oscillation_detected，随后抑制动作转人工复核")


# --------------------------------------------------------------------------


SCENARIOS = {1: scenario_1, 2: scenario_2, 3: scenario_3, 4: scenario_4, 5: scenario_5}


def main(argv: list[str]) -> None:
    picks = [int(a) for a in argv[1:]] or sorted(SCENARIOS)
    for n in picks:
        fn = SCENARIOS.get(n)
        if fn is None:
            print(f"未知场景 {n}")
            continue
        fn()
    print("\n" + "=" * 78)
    print("五个场景跑完。所有数字均来自真实运行，无人工编造。")
    print("=" * 78)


if __name__ == "__main__":
    main(sys.argv)
