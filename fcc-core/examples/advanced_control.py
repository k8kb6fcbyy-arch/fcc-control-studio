"""Entirely generic numerical demo of model-based controller primitives.

Run: python examples/advanced_control.py, after `pip install -e .`.
No real device or business-specific measurement/action assumptions.
"""
import numpy as np
from fcc import (StateSpace, DiscreteKalmanFilter, ConstrainedMPC,
                 RecursiveLeastSquares, SelfTuningARX, discrete_lqr,
                 lyapunov_certificate, frequency_response)

model=StateSpace([[.9]],[[.1]],[[1.]],domain='discrete',sample_time=1.0)
kf=DiscreteKalmanFilter(model,[[.01]],[[.05]],x=[0.],P=[[1.]])
mpc=ConstrainedMPC(model,8,Q=[[10.]],R=[[.1]],S=[[.05]],
                   u_min=[-5],u_max=[5],du_min=[-.8],du_max=[.8])
truth=np.array([0.])
u=np.array([0.])
for k in range(30):
    if k:
        kf.predict(u)  # Applied input over preceding sample interval.
    observed=truth[0]  # a simulator stand-in for a real sensor
    estimate=kf.update([observed],u)
    solution=mpc.solve(kf.x,[2.],u)
    u=solution['first_control']  # permitted only within this simulation
    truth,_=model.step(truth,u)
print('Final simulated output:',round(float(truth[0]),4))
print('LQR regulation gain:',discrete_lqr(model,[[2.]],[[1.]])['K'].tolist())
print('LTI stability certificate:',lyapunov_certificate(model,[[1.]])['certificate_valid'])
print('Low-frequency response:',frequency_response(model,[0.01])['magnitude'][0].tolist())

# RLS requires time-aligned *applied* input, not a predicted action.
rls=RecursiveLeastSquares([0.,0.,0.],np.eye(3)*1000.,forgetting_factor=.99)
y=0.
for i in range(120):
    applied_u=float(np.sin(i/5))
    y_next=.8*y+.2*applied_u+.01
    rls.update([y,applied_u,1],y_next)
    y=y_next
print('Identified ARX:',np.round(rls.theta,3).tolist())
print('Self tuning requires additional bounded, approved actuation; not executed.')
