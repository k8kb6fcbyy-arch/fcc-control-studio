"""Real FCC Client/Store/Engine contract, deliberately independent of a business."""
from __future__ import annotations
import numpy as np
import pytest
from fcc import Client
from fcc.actuator import ManualAdapter
from conftest import cfg, observe


def advanced_cfg(controller, params):
    c = cfg()
    c['goals']=[{'goal_id':'g1','target_variable':'temp','direction':'approach',
                 'setpoint':{'kind':'value','value':2.0},'tolerance':0.01}]
    c['controller']={'kind':controller,'params':params,'strategy_version':1}
    c['actuator']={'adapter':'manual','permission_mode':'require_approval',
                   'action_range':[-5.,5.], 'max_step':1.}
    return c


def test_mpc_loop_produces_action_and_persists_kalman(client):
    adapter=ManualAdapter(); adapter._value=0.0
    client.register_actuator('verified-input',adapter)
    data=advanced_cfg('mpc',{
        'model':{'A':[[.8]],'B':[[.2]],'C':[[1.]],'sample_time':1.},
        'Q':[[10.]],'R':[[.05]],'horizon':6,'u_min':[-5.],'u_max':[5.],
        'du_min':[-1.],'du_max':[1.],
        'kalman':{'Q':[[.01]],'R':[[.01]],'x0':[0.],'P0':[[1.]],'gate_nis':100.}})
    data['actuator']['adapter']='verified-input'
    loop=client.create_loop(data)
    observe(client,loop,'temp',0.)
    first=client.run_cycle(loop)
    assert first.estimated_state['method']=='kalman'
    assert first.estimated_state['accepted']
    assert len(first.proposed_actions)==1
    assert first.executed_actions[0]['status']=='awaiting_approval'
    state=client.store.get_controller_state(loop,'mpc',1)
    assert 'kalman' in state and state['kalman']['P']
    client.approve_action(first.executed_actions[0]['action_id'])
    assert adapter._value is not None


def test_adaptive_mode_stays_monitor_only_without_real_input_provenance(client):
    adapter=ManualAdapter();adapter._value=0.0
    client.register_actuator('stub',adapter)
    c=advanced_cfg('adaptive_arx',{'expected_gain_sign':1,'min_samples':12})
    c['actuator']['adapter']='stub'
    loop=client.create_loop(c)
    observe(client,loop,'temp',0.)
    one=client.run_cycle(loop)
    assert not one.proposed_actions
    assert one.estimated_state['method']=='rls_arx'
    assert one.estimated_state['samples']==0
    s=client.store.get_controller_state(loop,'adaptive_arx',1)
    assert s['prev_y']==0.


def test_engine_rejects_latest_ai_only_observation_even_with_kalman(client):
    adapter=ManualAdapter();adapter._value=0.
    client.register_actuator('stub',adapter)
    c=advanced_cfg('mpc',{'model':{'A':[[.8]],'B':[[.2]],'C':[[1.]],'sample_time':1.},
        'u_min':[-5],'u_max':[5],'kalman':{'Q':[[.1]],'R':[[1.]]}})
    c['actuator']['adapter']='stub'
    loop=client.create_loop(c)
    observe(client,loop,'temp',0.)
    client.submit_observation(loop,{'metric_id':'temp','value':0.1,'source_type':'ai_inference',
       'confidence':0.9,'evidence':['model-output'],'is_ground_truth':False})
    result=client.run_cycle(loop)
    assert result.proposed_actions==[]


def test_high_risk_control_requires_approval_even_when_requested_auto(client):
    from fcc.actuator import Actuator
    from fcc.models import ActuatorConfig, ActionProposal
    from fcc.actuator import AdapterRegistry
    act=Actuator(client.store,'L',ActuatorConfig(permission_mode='auto'),AdapterRegistry())
    p=ActionProposal(action_id='a',source_type='measured',risk_level='high',
      action_type='set_value',target='control_input',params={'value':1.},
      proposed_value=1.,goal_id='g1')
    d=act.decide(p)
    assert d.required_approval and d.mode=='require_approval'
