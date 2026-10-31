"""Independent mathematical reference and regression tests for v1.1."""
import math
import numpy as np
import pytest

from fcc.theory import (StateSpace, LuenbergerObserver, DigitalPID,
                        place_observer, place_state_feedback,
                        first_order_arx_identification)
from fcc.controllers import RuleController, ControllerContext
from fcc.models import ErrorRecord, GoalSpec, Setpoint
from fcc.scheduler import Scheduler
from fcc.actuator import Actuator, AdapterRegistry
from fcc.models import ActuatorConfig
from conftest import cfg, new_loop, observe, run


def test_continuous_stability_criteria_and_dimensions():
    stable = StateSpace([[-2]], [[1]], [[1]])
    assert stable.asymptotically_stable()
    assert stable.is_controllable() and stable.is_observable()
    assert not StateSpace([[0]], [[1]], [[1]]).asymptotically_stable()
    with pytest.raises(ValueError):
        StateSpace([[1, 0]], [[1]], [[1]])
    with pytest.raises(ValueError):
        StateSpace([[0]], [[1]], [[1]], [[1, 1]])


def test_zoh_analytic_first_order():
    m = StateSpace([[-2]], [[2]], [[1]])
    d = m.discretize(0.5)
    assert d.domain == "discrete" and d.asymptotically_stable()
    assert d.A[0, 0] == pytest.approx(math.exp(-1), abs=1e-12)
    assert d.B[0, 0] == pytest.approx(1-math.exp(-1), abs=1e-12)
    x1, y0 = d.step([0], [5])
    assert y0[0] == pytest.approx(0)
    assert x1[0] == pytest.approx(5*(1-math.exp(-1)))


def test_controllability_observability_rank():
    m = StateSpace([[0, 1], [-2, -3]], [[0], [1]], [[1, 0]])
    assert m.is_controllable() and m.is_observable()
    assert m.controllability_matrix().shape == (2, 2)
    assert m.observability_matrix().shape == (2, 2)
    bad = StateSpace([[0, 0], [0, -1]], [[1], [0]], [[1, 0]])
    assert not bad.is_controllable() and not bad.is_observable()
    with pytest.raises(ValueError):
        place_state_feedback(bad, [-1, -2])


def test_feedback_pole_placement():
    m = StateSpace([[0, 1], [-2, -3]], [[0], [1]], [[1, 0]])
    k = place_state_feedback(m, [-4, -5])
    assert np.sort(np.linalg.eigvals(m.state_feedback(k).A)).real == pytest.approx([-5, -4])
    assert m.state_feedback(k).asymptotically_stable()


def test_observer_residual_and_pole_location():
    m = StateSpace([[0.8]], [[0.5]], [[1]], domain="discrete", sample_time=1)
    l = place_observer(m, [0.1])
    obs = LuenbergerObserver(m, l, [0.0])
    assert obs.error_model_stable()
    result = obs.update([2.0], [1.0])
    assert result["innovation"][0] == pytest.approx(2)
    assert result["state_estimate_next"][0] == pytest.approx(1.9)
    with pytest.raises(ValueError):
        LuenbergerObserver(StateSpace([[-1]], [[1]], [[1]]), [[1]], [0])


def test_pid_derivative_on_measurement_avoids_setpoint_kick():
    pid = DigitalPID(kp=1, ki=0, kd=10, derivative_tau=0.1)
    a = pid.compute(0, 0, 1)
    b = pid.compute(100, 0, 1)
    assert a["derivative"] == 0
    assert b["output"] == pytest.approx(100)


def test_pid_conditional_antiwindup_and_bounds():
    pid = DigitalPID(kp=0, ki=10, output_min=0, output_max=1)
    for _ in range(20):
        result = pid.compute(10, 0, 1)
        assert result["output"] == pytest.approx(1)
        assert pid.integral == pytest.approx(0.1)
    pid2 = DigitalPID(kp=0.1, ki=0.5, output_min=0, output_max=3)
    for _ in range(50):
        assert 0 <= pid2.compute(100, 0, 1)["output"] <= 3
    assert pid2.integral == 0


def test_firstorder_id_with_excitation():
    rng = np.random.default_rng(4)
    u = rng.normal(size=100)
    y = np.zeros(101)
    for t in range(100):
        y[t+1] = 0.8*y[t] + 0.5*u[t] + 1
    fitted = first_order_arx_identification(y, u, 0.1)
    assert fitted["a"] == pytest.approx(0.8, abs=1e-10)
    assert fitted["b"] == pytest.approx(0.5, abs=1e-10)
    assert fitted["offset"] == pytest.approx(1, abs=1e-10)
    assert fitted["rmse"] < 1e-10
    with pytest.raises(ValueError):
        first_order_arx_identification([0]*8, [0]*7, 1)


def test_scheduler_threshold_mapping():
    assert Scheduler(None).should_run_on_threshold(
        type('Cfg', (), {'mode': 'threshold'})(), {'should_intervene': True})


def test_ai_metric_rule_refused_without_trust():
    goal = GoalSpec(goal_id='g', target_variable='m', setpoint=Setpoint(value=50))
    e = ErrorRecord(goal_id='g', target_variable='m', direction='approach',
                    value=10, error=40, out_of_tolerance=True, ai_only=True)
    ctx = ControllerContext(loop_id='L', cycle_seq=1, goals=[goal], errors=[e],
                            measurements={'m':10}, measurement_trust={'m':False})
    rule = RuleController({'rules':[{'if':{'metric':'m','op':'lt','value':30},
                                    'action':{'value':99,'goal_id':'g'}}]})
    assert rule.propose(ctx).proposals == []


def test_action_cannot_execute_before_approval(client):
    data = cfg()
    data['actuator']['permission_mode'] = 'require_approval'
    loop = new_loop(client, data)
    observe(client, loop, 'temp', 10)
    run(client, loop)
    aid = client.store.list_actions(loop)[0]['id']
    raw = Actuator(client.store, loop, ActuatorConfig(), client.engine.registry)
    direct = raw.execute(aid)
    assert direct['ok'] is False
    assert client.store.get_action(aid)['status'] == 'awaiting_approval'


def test_failed_rollback_not_misrepresented(client):
    class RejectRollback:
        def __init__(self): self.value = 0
        def apply(self, action): self.value = action['params']['value']; return {'ok':True}
        def state(self): return {'current_value': self.value}
        def rollback(self, action, result): return {'ok':False,'error':'not reversed'}
    data=cfg();data['actuator']['adapter']='reject_rb'
    loop=new_loop(client,data);client.register_actuator('reject_rb', RejectRollback())
    observe(client,loop,'temp',10);run(client,loop)
    aid=client.store.list_actions(loop)[0]['id']
    out=client.rollback_action(aid)
    assert out['ok'] is False
    assert client.store.get_action(aid)['status'] != 'rolled_back'


def test_pid_runtime_state_persists_to_restart(tmp_path):
    from fcc import Client
    db = str(tmp_path/'pid.db')
    c=Client(db);config=cfg();config['controller']={'kind':'pid','params':{'kp':1,'ki':0.2,
                                'sample_time_seconds':0.5},'strategy_version':1}
    loop=c.create_loop(config);c.submit_observation(loop,{'metric_id':'temp','value':20,'source_type':'measured'})
    c.run_cycle(loop)
    before=c.store.get_controller_state(loop,'pid',1)
    assert before['integral'] == pytest.approx(15.0)
    c.close()
    next_client=Client(db)
    assert next_client.store.get_controller_state(loop,'pid',1) == before
    next_client.close()


def test_current_ai_reading_cannot_use_older_measured_sample_as_authorization(client):
    data=cfg()
    loop=new_loop(client,data)
    observe(client,loop,'temp',25.0,'measured')
    observe(client,loop,'temp',10.0,'ai_inference', confidence=0.91, evidence=['unverified suggestion'])
    outcome=run(client,loop)
    assert outcome.executed_actions == []
    assert any('untrusted_current_observation' in e for e in outcome.guard_events)


def test_flagged_outlier_does_not_verify_action_as_improvement(client):
    from fcc.verification import Verifier
    from fcc.observations import ObservationManager
    from fcc.models import ObservationSpec
    from fcc.goals import GoalManager
    config=cfg();config['observations'][0]['outlier_policy']='flag'
    loop=new_loop(client,config)
    observe(client,loop,'temp',10.0)
    run(client,loop)
    for _ in range(4): observe(client,loop,'temp',10.0)
    report=observe(client,loop,'temp',49.0)
    assert 'outlier' in report['quality_flags']
    mgr=ObservationManager(client.store,loop,[ObservationSpec(**config['observations'][0])])
    verifier=Verifier(client.store,loop,mgr)
    act=client.store.list_actions(loop)[0]
    goal=GoalSpec.model_validate(config['goals'][0])
    verdict=verifier.judge(act,goal,mgr.snapshot('temp'))
    assert verdict.verdict=='inconclusive'


def test_range_goal_tolerance_applies_to_both_ends():
    from fcc.goals import within_tolerance,GoalManager
    from fcc.models import Setpoint
    p=Setpoint(kind='range',low=10,high=20)
    assert within_tolerance(p,9.6,.5)
    assert within_tolerance(p,20.4,.5)
    assert not within_tolerance(p,20.6,.5)
    goal=GoalSpec(goal_id='r',target_variable='v',setpoint=p,tolerance=.5)
    assert GoalManager([goal]).target_snapshot()['r']['band']==[9.5,20.5]


def test_ai_advisor_rejects_nonfinite_confidence_and_value():
    from fcc.controllers import AIAssistedController
    out=AIAssistedController({'static_suggestions':[
      {'confidence':float('nan'),'evidence':['test'],'value':20},
      {'confidence':.95,'evidence':['test'],'value':float('inf')},
      {'confidence':.95,'evidence':['test'],'value':20},
    ]}).propose(ControllerContext(
      loop_id='L',cycle_seq=1,goals=[GoalSpec(goal_id='g', target_variable='v',setpoint=Setpoint(value=10))],
      errors=[],measurements={'v':5}))
    assert len(out.proposals)==1
    assert out.proposals[0].proposed_value==20
