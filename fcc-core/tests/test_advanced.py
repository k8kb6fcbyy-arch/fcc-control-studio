"""Independent numerical baselines, negative cases and a cross-module smoke test."""
import numpy as np
import pytest
from numpy.testing import assert_allclose

from fcc.theory import StateSpace
from fcc.advanced import (DiscreteKalmanFilter, ExtendedKalmanFilter, ConstrainedMPC,
                          RecursiveLeastSquares, SelfTuningARX, discrete_lqr)
from fcc.controllers import MPCController, AdaptiveARXController, ControllerContext
from fcc.models import ErrorRecord, GoalSpec, Setpoint


def plant(A=[[0.85]],B=[[0.15]],C=[[1.]],D=None):
    return StateSpace(A,B,C,D,domain='discrete',sample_time=1.0)


def test_kf_exact_scalar_update_and_joseph_psd():
    kf=DiscreteKalmanFilter(plant([[1.]],[[1.]]),[[0.1]],[[1.]], [0.], [[1.]])
    r=kf.update([1.],[0.])
    assert_allclose(r['x'], [0.5],atol=1e-12)
    assert_allclose(r['P'], [[0.5]],atol=1e-12)
    kf.predict([1.])
    assert_allclose(kf.x,[1.5])
    assert_allclose(kf.P,[[0.6]])
    assert_allclose(kf.update([2.],[0.])['x'],[1.6875])
    assert np.min(np.linalg.eigvalsh(kf.P))>=0


def test_kf_missing_multisensor_and_gating():
    m=plant(A=[[1.,0.],[0.,1.]],B=[[1.],[0.]],C=[[1.,0.],[0.,1.]])
    kf=DiscreteKalmanFilter(m,np.eye(2)*0.1,np.eye(2)*0.1,[0,0],np.eye(2))
    r=kf.update([1.,None],[0.])
    assert r['used_channels']==[0]
    assert kf.x[0]>0 and kf.x[1]==0
    x=kf.x.copy()
    res=kf.update([1e3,None], [0.],gate_nis=5.)
    assert res['accepted'] is False
    assert_allclose(kf.x,x)
    assert kf.update([None,None])['accepted'] is False


def test_kf_reject_invalid_covariance_and_continuous_model():
    with pytest.raises(ValueError):
        DiscreteKalmanFilter(plant(),[[-1.]],[[1.]], [0.], [[1.]])
    with pytest.raises(ValueError):
        DiscreteKalmanFilter(plant(),[[.1]],[[0.]], [0.], [[1.]])
    with pytest.raises(ValueError):
        DiscreteKalmanFilter(StateSpace([[-1.]],[[1.]],[[1.]]),[[.1]],[[1.]], [0.], [[1.]])


def test_ekf_linear_equivalence_and_jacobian_guard():
    ekf=ExtendedKalmanFilter(lambda x,u: .9*x+u,
        lambda x,u:x, lambda x,u: np.array([[.9]]),
        lambda x,u:np.array([[1.]]), [[.2]], [[.5]], [0.], [[1.]])
    lin=DiscreteKalmanFilter(plant([[.9]],[[1.]]), [[.2]],[[.5]], [0.], [[1.]])
    ekf.predict([1.]); lin.predict([1.])
    e=ekf.update([.9],[0]); l=lin.update([.9],[0])
    assert_allclose(e['x'],l['x']); assert_allclose(e['P'],l['P'])
    bad=ExtendedKalmanFilter(lambda x,u:x, lambda x,u:x, lambda x,u:[[1,0]],
          lambda x,u:[[1]],[[.1]],[[1.]],[0.],[[1.]])
    with pytest.raises(ValueError,match='jac_f'):
        bad.predict([0.])


def test_mpc_unconstrained_feedback_tracks_reference():
    model=plant()
    opt=ConstrainedMPC(model,8,[[10.]],[[.01]],S=[[.01]],Qf=[[15.]],
                       u_min=[0.],u_max=[10.],du_min=[-2.],du_max=[2.])
    x=np.array([0.]); u=np.array([0.])
    seen=[]
    for k in range(22):
        res=opt.solve(x,[4.],u)
        assert res['success']
        new=res['first_control']
        assert 0<=new[0]<=10+1e-7
        assert abs(new[0]-u[0])<=2+1e-6
        x,_=model.step(x,new)
        seen.append(x[0]);u=new
    assert abs(seen[-1]-4.) < 0.07


def test_mpc_mimo_output_constraints_and_infeasible():
    m=plant(A=[[0.8,0],[0,0.6]],B=[[0.2,0],[0,0.4]],C=np.eye(2).tolist())
    c=ConstrainedMPC(m,4,np.eye(2),np.eye(2)*.1,u_min=[0,0],u_max=[2,1],
                     y_min=[0,0],y_max=[.75,.75])
    o=c.solve([0.,0.],[.6,.5],[0.,0.])
    assert o['plan'].shape==(4,2)
    assert np.all(o['predicted_outputs'] <= .750001)
    with pytest.raises(RuntimeError, match='MPC'):
        # y_next is necessarily >=0 but y_max is negative; impossible.
        ConstrainedMPC(plant(),3,[[1.]],[[1.]],u_min=[0.],u_max=[1.],
                       y_max=[-1.]).solve([0.],[.8],[0.])


def test_mpc_reject_wrong_domains_bounds_and_shapes():
    with pytest.raises(ValueError):
        ConstrainedMPC(StateSpace([[-1.]],[[1.]],[[1.]]),3,[[1.]],[[1.]])
    with pytest.raises(ValueError):
        ConstrainedMPC(plant(),3,[[-1.]],[[1.]])
    with pytest.raises(ValueError):
        ConstrainedMPC(plant(),3,[[1.]],[[1.]],u_min=[1.],u_max=[0.])
    with pytest.raises(ValueError):
        ConstrainedMPC(plant(),3,[[1.]],[[1.]]).solve([0.],[1.,2.],[0.])


def test_lqr_stability_and_uncontrollable_rejection():
    good=discrete_lqr(plant([[1.1]],[[1.]],[[1.]]),[[1.]],[[1.]])
    assert abs(good['closed_loop_poles'][0]) < 1
    with pytest.raises(Exception):
        discrete_lqr(plant([[1.1]],[[0.]],[[1.]]),[[1.]],[[1.]])


def test_rls_identifies_arx_with_exciting_input():
    rng=np.random.default_rng(1001)
    model=RecursiveLeastSquares([0.,0.,0.],np.eye(3)*1000., .995)
    y=0.
    for _ in range(500):
        u=float(rng.uniform(-2,2))
        nxt=.72*y+.48*u+.13
        model.update([y,u,1],nxt)
        y=nxt
    assert_allclose(model.theta,[.72,.48,.13],atol=.006)
    assert np.all(np.linalg.eigvalsh(model.P)>0)


def test_adaptive_requires_verified_data_and_gain_direction():
    rls=RecursiveLeastSquares([.8,-.4,.1],np.eye(3)*.1)
    ctrl=SelfTuningARX(rls,expected_gain_sign=1,min_samples=12,samples=30)
    with pytest.raises(RuntimeError,match='sign'):
        ctrl.propose(0,1,0,u_min=-5,u_max=5,max_step=1)
    ctrl.rls.theta=np.array([.8,.4,.1])
    plan=ctrl.propose(0,1,0,u_min=-5,u_max=5,max_step=.5)
    assert plan['control']==pytest.approx(.5)
    ctrl.samples=1
    with pytest.raises(RuntimeError,match='Insufficient'):
        ctrl.propose(0,1,0,u_min=-5,u_max=5,max_step=1)


def ctx(y, controller_input=0., trust=True):
    goal=GoalSpec(goal_id='g',target_variable='y',setpoint=Setpoint(value=2.),tolerance=.01)
    err=ErrorRecord(goal_id='g',target_variable='y',direction='approach',error=2.-y,
          tolerance=.01,out_of_tolerance=True,value=y,setpoint={'kind':'value','value':2.})
    return ControllerContext('loop',1,[goal],[err],{'y':y},measurement_trust={'y':trust},
                     current_control=controller_input,action_range=(-5,5),max_step=1.0)


def test_mpc_controller_integrates_with_context_and_provenance():
    config={'model':{'A':[[.8]],'B':[[.2]],'C':[[1.]],'sample_time':1.},
            'Q':[[10.]],'R':[[.1]],'horizon':4,'u_min':-5,'u_max':5,
            'du_min':-1,'du_max':1}
    c=MPCController(config)
    result=c.propose(ctx(0.))
    assert result.proposals
    assert result.proposals[0].origin_detail.startswith('mpc')
    assert result.estimate['predicted_outputs']
    assert MPCController(config).propose(ctx(0.,trust=False)).proposals==[]


def test_mpc_controller_with_kalman_persists_and_rejects_gated_measurement():
    config={'model':{'A':[[.8]],'B':[[.2]],'C':[[1.]],'sample_time':1.},
            'Q':[[10.]],'R':[[.1]],'u_min':-5,'u_max':5,
            'kalman':{'Q':[[.1]],'R':[[1.]],'gate_nis':4.,'x0':[0.],'P0':[[1.]]}}
    first=MPCController(config).propose(ctx(0.))
    assert first.next_state['kalman']
    config['_runtime_state']=first.next_state
    nxt=MPCController(config).propose(ctx(200.))
    assert not nxt.proposals
    assert nxt.estimate['accepted'] is False


def test_adaptive_controller_stays_monitor_without_persisted_excitation():
    c=AdaptiveARXController({'expected_gain_sign':1,'min_samples':12})
    res=c.propose(ctx(0.))
    assert res.next_state and not res.proposals
    assert res.estimate['samples']==0


def test_ukf_matches_linear_filter_with_identity_model():
    from fcc.advanced import UnscentedKalmanFilter
    ukf=UnscentedKalmanFilter(lambda x,u:x+u,lambda x,u:x,[[.1]],[[.2]],[0.],[[1.]])
    kf=DiscreteKalmanFilter(plant(A=[[1.]],B=[[1.]]),[[.1]],[[.2]],[0.],[[1.]])
    for sample in [1.1,1.8,2.8]:
        ukf.predict([1.]);kf.predict([1.])
        out=ukf.update([sample],[0]);truth=kf.update([sample],[0])
        assert_allclose(out['x'],truth['x'],atol=1e-7)
        assert_allclose(out['P'],truth['P'],atol=1e-7)


def test_ukf_parameter_validation_and_gating():
    from fcc.advanced import UnscentedKalmanFilter
    with pytest.raises(ValueError):
        UnscentedKalmanFilter(lambda x,u:x,lambda x,u:x,[[.1]],[[1.]],[0.],[[1.]],alpha=0)
    f=UnscentedKalmanFilter(lambda x,u:x,lambda x,u:x,[[.1]],[[.1]],[0.],[[1.]])
    f.predict([0])
    r=f.update([1000],[0],gate_nis=9.)
    assert r['accepted'] is False
    assert_allclose(f.x,[0.],atol=1e-12)
