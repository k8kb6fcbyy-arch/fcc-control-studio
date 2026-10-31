"""派单包 §9 的 16 个场景，一个场景一个测试（A14 要求覆盖）。

这些用例全部走真实闭环（simplant / 真实 store / 真实控制器），不 mock 核心逻辑。
"""

from __future__ import annotations

import datetime as dt

import pytest

from conftest import cfg, new_loop, observe, patch, run
from fcc import Client, util
from fcc.simplant import SimulatedPlant

PLANT_CFG = dict(tau=3.0, dead_time=1.0, noise_sigma=0.0, x0=0.0)


def _plant_loop(client: Client, *, gain=0.5, tolerance=1.0, deadband=0.3,
                law="tracking", plant_kwargs=None,
                **actuator) -> tuple[str, SimulatedPlant]:
    plant = SimulatedPlant(**(plant_kwargs or PLANT_CFG))
    data = cfg()
    data["goals"][0]["tolerance"] = tolerance
    data["controller"]["params"].update({
        "proportional_gain": gain, "deadband": deadband,
        "hysteresis": 0.3, "cooldown_cycles": 0, "law": law})
    data["actuator"].update({"adapter": "plant", **actuator})
    loop = client.create_loop(data)
    client.register_actuator("plant", plant)
    return loop, plant


def _step(client: Client, plant: SimulatedPlant, loop: str):
    plant.advance()
    reading = plant.read()
    observe(client, loop, "temp", reading["value"])
    return run(client, loop), reading["value"]


# 1 -------------------------------------------------------------------
def test_01_normal_stable_run(client):
    loop, plant = _plant_loop(client)
    trace = []
    for _ in range(12):
        res, y = _step(client, plant, loop)
        trace.append(round(y, 2))
    assert abs(trace[-1] - 50.0) <= 1.0, trace
    assert res.errors[0]["out_of_tolerance"] is False
    assert res.verification_status in ("no_action", "verified:improved",
                                       "verified:unchanged", "pending_verification")


# 2 -------------------------------------------------------------------
def test_02_setpoint_changes_suddenly(client):
    loop, plant = _plant_loop(client)
    for _ in range(10):
        _step(client, plant, loop)
    client.update_config(loop, {"goals": [{
        "goal_id": "g1", "target_variable": "temp", "direction": "approach",
        "setpoint": {"kind": "value", "value": 70.0}, "tolerance": 1.0}]})
    res, y = _step(client, plant, loop)
    assert res.errors[0]["error"] > 0
    assert res.should_intervene is True
    for _ in range(12):
        _, y = _step(client, plant, loop)
    assert abs(y - 70.0) <= 1.5, y


# 3 -------------------------------------------------------------------
def test_03_noise_increases(client):
    loop, plant = _plant_loop(client)
    for _ in range(10):
        _step(client, plant, loop)
    plant.set_noise(1.5)                     # 0 -> 1.5，约 5 倍量级
    actions = 0
    for _ in range(12):
        res, _ = _step(client, plant, loop)
        actions += len([a for a in res.executed_actions
                        if a["status"] == "pending_verification"])
    # 死区 + 迟滞：噪声放大后不应每轮都动手
    assert actions <= 6, f"噪声下动作次数过多: {actions}/12"
    err = res.errors[0]
    assert abs(err["error"]) <= max(3.0, err["tolerance"] * 3)


# 4 -------------------------------------------------------------------
def test_04_data_missing_three_cycles(client):
    data = cfg()
    data["observations"][0].update({"freshness_limit": 1.0, "missing_policy": "hold_last"})
    loop = new_loop(client, data)
    old = util.to_iso(util.utcnow() - dt.timedelta(seconds=5))
    observe(client, loop, "temp", 10.0, ts=old)
    for _ in range(3):
        res = run(client, loop)
        assert res.errors[0]["stale"] is True
        assert res.should_intervene is False       # 不因缺失而误判
        assert res.executed_actions == []


# 5 -------------------------------------------------------------------
def test_05_outlier_observation(client):
    data = cfg()
    data["observations"][0]["outlier_policy"] = "flag"
    loop = new_loop(client, data)
    for v in (10.0, 10.2, 9.8, 10.1, 9.9, 10.05):
        observe(client, loop, "temp", v)
    res = observe(client, loop, "temp", 900.0)
    assert "outlier" in res["quality_flags"]
    cycle = run(client, loop)
    assert cycle.errors[0]["error"] is not None    # 仍按 flag 策略参与计算，但留了痕迹


def test_05b_outlier_drop_policy_blocks_action(client):
    data = cfg()
    data["observations"][0].update({"outlier_policy": "drop", "valid_range": [0, 100]})
    loop = new_loop(client, data)
    for v in (10.0, 10.2, 9.8, 10.1):
        observe(client, loop, "temp", v)
        run(client, loop)
    observe(client, loop, "temp", 5000.0)
    res = run(client, loop)
    assert res.errors[0]["missing"] is True
    assert res.executed_actions == []


# 6 -------------------------------------------------------------------
def test_06_observation_delay(client):
    """时滞 3 个周期：控制器只能看到旧值，系统仍要能收敛而不是发散。"""
    loop, plant = _plant_loop(client, plant_kwargs=dict(tau=3.0, dead_time=3.0,
                                                        noise_sigma=0.0, x0=0.0))
    trace = []
    for _ in range(20):
        _, y = _step(client, plant, loop)
        trace.append(y)
    assert abs(trace[-1] - 50.0) <= 5.0, trace[-5:]


# 7 -------------------------------------------------------------------
def test_07_action_execution_failure(client):
    loop, plant = _plant_loop(client, max_retries=1)
    observe(client, loop, "temp", 10.0)
    plant.inject_failures(5)
    res = run(client, loop)
    action = client.store.list_actions(loop)[0]
    assert action["status"] == "failed"
    assert action["verification_status"] == "failed"
    assert res.verification_status == "failed"
    assert client.get_verification_report(loop)["counts"]["improved"] == 0


# 8 -------------------------------------------------------------------
def test_08_external_disturbance(client):
    """持续外部扰动下要回到设定值 -> 必须用 incremental 律。

    tracking 律是"P + 设定值前馈"，对恒定扰动必然留静差（y = sp + d/(1+K)），
    这是 P 控制的教科书写法，不是 bug。要消静差就得有积分作用，
    而真正的 PID 在 P1 是 interface_only —— 于是这里用增量式（等效积分）代替，
    并在报告 §6 里把这件事讲清楚。
    """
    loop, plant = _plant_loop(client, law="incremental", gain=0.15)
    for _ in range(10):
        _step(client, plant, loop)
    plant.inject_disturbance(-15.0)        # 突然掉温
    res, y = _step(client, plant, loop)
    for _ in range(14):
        res, y = _step(client, plant, loop)
    assert abs(y - 50.0) <= 2.0, y
    assert res.errors[0]["out_of_tolerance"] is False


# 9 -------------------------------------------------------------------
def test_09_model_estimation_error(client):
    """P1 没有模型估计（卡尔曼 interface_only），这里测「观测本身有偏」。

    系统只能基于观测动作；偏差导致它把 45 当成 45（真实 50），
    验证结论不应出现 improved —— 因为重新观测也没变好。
    """
    loop = new_loop(client)
    observe(client, loop, "temp", 45.0)      # 有偏的观测
    r1 = run(client, loop)
    assert r1.errors[0]["error"] == pytest.approx(5.0)
    observe(client, loop, "temp", 45.0)
    r2 = run(client, loop)
    assert all(v["verdict"] != "improved" for v in r2.verifications)


# 10 ------------------------------------------------------------------
def test_10_too_high_frequency_is_throttled(client):
    data = cfg()
    data["scheduler"] = {"mode": "fixed", "interval_seconds": 3600}
    loop = new_loop(client, data)
    client.start_loop(loop)
    ran_first = client.engine.tick()
    ran_second = client.engine.tick()        # 1 小时内不该再跑
    assert ran_first == [loop]
    assert ran_second == []
    assert client.store.cycle_count(loop) == 1


# 11 ------------------------------------------------------------------
def test_11_over_correction_oscillation(client):
    loop, plant = _plant_loop(client, gain=0.9,
                              plant_kwargs=dict(tau=3.0, dead_time=2.0,
                                                noise_sigma=0.0, x0=0.0))
    oscillated = False
    for _ in range(16):
        res, _ = _step(client, plant, loop)
        if res.errors[0]["oscillation_detected"]:
            oscillated = True
            break
    assert oscillated, "高增益 + 大时滞应该被识别为振荡"
    assert any(a.startswith("oscillation:") for a in res.anomalies)


def test_11b_oscillation_suppresses_further_action(client):
    data = cfg()
    data["controller"]["params"].update({"proportional_gain": 0.9})
    loop = new_loop(client, data)
    seq = [10, 90, 10, 90, 10, 90, 10, 90, 10, 90]
    for v in seq:
        observe(client, loop, "temp", v)
        res = run(client, loop)
    assert res.errors[0]["oscillation_detected"] is True
    assert res.executed_actions == []


# 12 ------------------------------------------------------------------
def test_12_multi_goal_conflict(client):
    data = cfg()
    data["goals"][0]["direction"] = "increase"
    data["goals"].append({"goal_id": "g2", "target_variable": "temp",
                          "direction": "decrease",
                          "setpoint": {"kind": "value", "value": 45.0},
                          "tolerance": 1.0})
    with pytest.raises(Exception) as ei:
        new_loop(client, data)
    assert "目标冲突" in str(ei.value)


def test_12b_multi_goal_non_conflict_runs_independently(client):
    data = cfg()
    data["observations"].append({"metric_id": "humid", "source": "manual",
                                 "freshness_limit": 600})
    data["goals"].append({"goal_id": "g2", "target_variable": "humid",
                          "direction": "decrease",
                          "setpoint": {"kind": "value", "value": 40.0},
                          "tolerance": 5.0, "priority": 1})
    loop = new_loop(client, data)
    observe(client, loop, "temp", 10.0)
    observe(client, loop, "humid", 60.0)
    res = run(client, loop)
    assert len(res.errors) == 2
    assert len(res.executed_actions) == 2


# 13 ------------------------------------------------------------------
def test_13_user_rejects_adjustment(client):
    data = cfg()
    data["actuator"]["permission_mode"] = "require_approval"
    loop = new_loop(client, data)
    observe(client, loop, "temp", 10.0)
    run(client, loop)
    aid = client.store.list_actions(loop)[0]["id"]
    client.reject_action(aid, "operator", "现在不能调")
    action = client.store.get_action(aid)
    assert action["status"] == "rejected"
    assert action["approval_by"] == "operator"

    observe(client, loop, "temp", 10.0)
    res = run(client, loop)
    # 被驳回后不会偷偷执行；但同一目标仍待处理时会提示需要人工
    assert all(a["status"] == "rejected" for a in client.store.list_actions(loop)
               if a["id"] == aid)
    assert res.human_review_required is True


# 14 ------------------------------------------------------------------
def test_14_strategy_upgrade_regression_then_rollback(client):
    data = cfg()
    data["controller"]["params"]["proportional_gain"] = 0.5
    loop = new_loop(client, data)
    observe(client, loop, "temp", 10.0)
    run(client, loop)                       # v1 正常

    # 升级到 v2：增益过大 + 反向，导致恶化
    client.update_config(loop, {"controller": {
        "kind": "threshold",
        "params": {"proportional_gain": -0.5, "deadband": 0.0,
                   "hysteresis": 0.0, "cooldown_cycles": 0, "law": "tracking"},
        "strategy_version": 2}})
    observe(client, loop, "temp", 20.0)
    run(client, loop)
    observe(client, loop, "temp", 5.0)
    res = run(client, loop)
    assert any(v["verdict"] == "worsened" for v in res.verifications), res.verifications

    out = client.rollback_strategy(loop, 1)
    assert out["ok"] is True
    restored = client.store.get_loop_config(loop)[0]
    assert restored.controller.strategy_version == 1
    assert restored.controller.params["proportional_gain"] == 0.5


# 15 ------------------------------------------------------------------
def test_15_process_restart_recovery(tmp_path):
    path = str(tmp_path / "recover.db")
    c1 = Client(path)
    loop = new_loop(c1)
    observe(c1, loop, "temp", 10.0)
    run(c1, loop)
    c1.start_loop(loop)
    c1.close()

    c2 = Client(path)
    conf, enabled, _ = c2.store.get_loop_config(loop)
    assert enabled is True
    assert conf.goals[0].setpoint.value == 50.0
    assert c2.store.cycle_count(loop) == 1
    res = run(c2, loop)                     # 重启后还能继续跑
    assert res.cycle_id == 2
    c2.close()


# 16 ------------------------------------------------------------------
def test_16_duplicate_event_idempotent(client):
    loop = new_loop(client)
    observe(client, loop, "temp", 10.0)
    r1 = client.run_cycle(loop, event_key="dup-1")
    r2 = client.run_cycle(loop, event_key="dup-1")
    assert r1.cycle_id == r2.cycle_id
    assert client.store.cycle_count(loop) == 1
    assert len(client.store.list_actions(loop)) == 1

    fb1 = client.submit_human_feedback(loop, {"value": 30.0, "goal_id": "g1",
                                              "idempotency_key": "fb-1"})
    fb2 = client.submit_human_feedback(loop, {"value": 30.0, "goal_id": "g1",
                                              "idempotency_key": "fb-1"})
    assert fb1["ok"] is True and fb2["ok"] is False
    assert len(client.store.list_actions(loop)) == 2
