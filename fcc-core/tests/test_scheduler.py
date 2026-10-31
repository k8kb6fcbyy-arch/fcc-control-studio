"""Scheduler：手动 / 固定周期 / 事件 / 阈值，以及只留接口的那几个。"""

from __future__ import annotations

import datetime as dt

import pytest

from conftest import cfg, new_loop, observe, run
from fcc import util
from fcc.models import SchedulerConfig
from fcc.scheduler import CronTrigger, Scheduler, TrendTrigger


def test_manual_mode_is_never_due(client):
    loop = new_loop(client)
    sch = Scheduler(client.store)
    assert sch.is_due(loop, SchedulerConfig(mode="manual")) is False


def test_fixed_mode_due_when_no_cycle_yet(client):
    loop = new_loop(client)
    assert Scheduler(client.store).is_due(loop, SchedulerConfig(mode="fixed")) is True


def test_fixed_mode_interval(client):
    data = cfg()
    data["scheduler"] = {"mode": "fixed", "interval_seconds": 60}
    loop = new_loop(client, data)
    run(client, loop)
    sch = Scheduler(client.store)
    conf = SchedulerConfig(mode="fixed", interval_seconds=60)
    now = util.utcnow_iso()
    assert sch.is_due(loop, conf, now) is False
    later = util.to_iso(util.parse_iso(now) + dt.timedelta(seconds=61))
    assert sch.is_due(loop, conf, later) is True


def test_interval_unit_conversion():
    assert SchedulerConfig(interval_seconds=2, interval_unit="minute").interval_in_seconds() == 120
    assert SchedulerConfig(interval_seconds=1, interval_unit="hour").interval_in_seconds() == 3600


def test_next_evaluation_at_for_fixed(client):
    data = cfg()
    data["scheduler"] = {"mode": "fixed", "interval_seconds": 30}
    loop = new_loop(client, data)
    res = run(client, loop)
    assert res.next_evaluation_at is not None
    gap = util.seconds_between(res.started_at, res.next_evaluation_at)
    assert gap == pytest.approx(30.0, abs=0.01)


def test_event_trigger_flag(client):
    sch = Scheduler(client.store)
    assert sch.should_run_on_event(SchedulerConfig(mode="event")) is True
    assert sch.should_run_on_event(SchedulerConfig(mode="fixed")) is False


def test_threshold_trigger_uses_last_result(client):
    loop = new_loop(client)
    observe(client, loop, "temp", 10.0)
    res = run(client, loop)
    sch = Scheduler(client.store)
    conf = SchedulerConfig(mode="threshold")
    assert sch.should_run_on_threshold(conf, res) is True
    res.should_intervene = False
    assert sch.should_run_on_threshold(conf, res) is False


def test_tick_only_runs_enabled_fixed_loops(client):
    data = cfg()
    data["scheduler"] = {"mode": "fixed", "interval_seconds": 0.001}
    loop = new_loop(client, data)
    assert client.engine.tick() == []          # 未启动
    client.start_loop(loop)
    assert client.engine.tick() == [loop]


def test_adaptive_cron_trend_are_operational(client):
    sch=Scheduler(client.store)
    assert sch.adaptive_interval(60.,severity=2,min_seconds=5,max_seconds=600)==pytest.approx(12.)
    assert CronTrigger('*/5 * * * *').next_at('2026-10-31T12:01:15+00:00') == '2026-10-31T12:05:00+00:00'
    assert TrendTrigger(window=4,min_abs_slope=.5).should_fire([0,1,2,3])
    assert not TrendTrigger(window=4,min_abs_slope=.5).should_fire([1,1,1,1])
    assert CronTrigger.STATUS == 'implemented'
    assert TrendTrigger.STATUS == 'implemented'


def test_adaptive_tick_and_cron_tick(client):
    data=cfg()
    data['scheduler']={'mode':'adaptive','interval_seconds':10,'min_interval_seconds':1,'max_interval_seconds':30}
    loop=new_loop(client,data)
    run(client,loop)
    sch=Scheduler(client.store)
    later=util.to_iso(util.parse_iso(util.utcnow_iso()) + dt.timedelta(seconds=12))
    assert sch.is_due(loop, SchedulerConfig(**data['scheduler']),later)
    assert CronTrigger('0 8 * * 1',timezone='America/New_York').next_at('2026-10-31T15:00:00+00:00').startswith('2026-11-02T08:00:00')
