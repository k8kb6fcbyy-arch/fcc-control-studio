# Advanced algorithms — implementation contracts and required preconditions

This document describes **actual FCC Core 1.2 public interfaces**. `Q` can refer to different matrices depending on the algorithm: KF process noise covariance, MPC output tracking weight, or LQR state cost. Never transfer a numerical `Q` unchanged between algorithms.

## Constrained MPC

```python
from fcc import StateSpace, ConstrainedMPC
model = StateSpace(A=[[.9]], B=[[.1]], C=[[1.]],
                   domain='discrete', sample_time=1.0)
controller = ConstrainedMPC(model, horizon=8, Q=[[10]], R=[[.1]], S=[[.1]],
                            u_min=[-5], u_max=[5], du_min=[-1], du_max=[1])
plan = controller.solve(x0=[0], reference=[2], u_prev=[0])
first = plan['first_control']     # model output only; not yet applied
```

For m inputs and p outputs, `u_min/max` and `du_min/max` are m-vectors, `y_min/max` are p-vectors, `Q/Qf` are p×p PSD, `R` is m×m positive definite, `S` is m×m PSD. The model can have n states. `reference` is p-vector or horizon×p; all values must be numerical and time-aligned. Solve failure → hold and request human review. Returned `plan` and `predicted_outputs` are model predictions, not verified outcomes.

**Engine SISO controller config** (`kind=mpc`, inside `params`):

`model:{A,B,C,D?,sample_time}, horizon?, Q?, R?, S?, Qf?, u_min, u_max, du_min?, du_max?, y_min?, y_max?, max_dt_error_ratio?, kalman?`

Current actuator value **must** be available via registered actuator `state().current_value`. Optional Kalman: `kalman:{Q,R,x0?,P0?,gate_nis?}`. Q and R here mean noise covariance. SISO Engine cannot apply MIMO plan from its scalar built-in action. Engine does not support arbitrary observer model functions in JSON.

## KF / EKF / UKF

```python
from fcc import DiscreteKalmanFilter
kf = DiscreteKalmanFilter(model, Q=[[.01]], R=[[.05]], x=[0], P=[[1]])
first = kf.update([0.0],[0.0]) # first sensor sample at x0 time index
kf.predict([0.5])              # u applied during previous sample interval
next = kf.update([0.2],[0.5],gate_nis=9.0)
```

KF assumes additive white process & measurement noise and uses a Joseph covariance update; supports missing measurement channel as `None`/NaN. `gate_nis` is an **absolute** chi-square statistic cutoff to select by number of available channels. Do not label filter output 'measured truth'. For EKF, `ExtendedKalmanFilter(f,h,jac_f,jac_h,Q,R,x,P)`. For UKF, `UnscentedKalmanFilter(f,h,Q,R,x,P,alpha,beta,kappa)`. Model functions are trusted Python integration points only.

## Adaptive ARX / RLS

```python
import numpy as np
from fcc import RecursiveLeastSquares, SelfTuningARX
rls = RecursiveLeastSquares(theta=[0.,0.,0.], P=np.eye(3)*1000,
                            forgetting_factor=.99)
rls.update(phi=[y_k, real_applied_u_k, 1.0], y_next=y_next)
```

`SelfTuningARX(rls, expected_gain_sign=1, min_samples=12)` takes measured transitions through `observe_transition(y_prev,u_applied,y_next)`; `propose(y_now,target_next,u_current,u_min,u_max,max_step)` returns a guarded suggested action. Insufficient samples/sign mismatch/weak gain/invalid bounds => rejection. The Engine `kind=adaptive_arx` requires `params.expected_gain_sign` as ±1, `trusted_applied_input=true` **only** if actual actuator readback really corresponds to each interval, and `actuator.action_range + max_step`. Default is **no auto-adaptation**, not a mock controller. High-risk changes are routed for human approval regardless of `permission_mode=auto`.

## LQR, Lyapunov, linearization, transfer, frequency response

- `discrete_lqr(model, Q, R)` returns K, P, eigenvalues for regulator `u=-Kx`.
- `lyapunov_certificate(model,Q)` gives matrix and certificate for a **known** continuous/discrete LTI system.
- `linearize(f,x0,u0,epsilon,sample_time?)` computes numerical Jacobians for local dynamics, without claiming global validity.
- `state_space_to_transfer_function(model)` supports SISO.
- `frequency_response(model, angular_frequencies)` is complex MIMO frequency response; discrete frequencies restricted to below Nyquist.
- `step_response(model,steps,amplitude,x0)` is a **simulated** SISO output trace, not actual system output.

## Four principles for every advanced method

1. Parameter values are *hypotheses* until validated on independent process data.
2. Model output and optimizer success are not evidence of physical improvement.
3. If time base, applied input, model, or observability fails, do not generate an autonomous corrective action.
4. Native Engine is a scalar action pipeline; model mathematics may be multidimensional, but external adapters must bridge it explicitly with safe approvals and measured feedback.
