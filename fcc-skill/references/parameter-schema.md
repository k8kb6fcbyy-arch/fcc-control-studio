# Generic config parameters (FCC Core 1.2)

| Area | Mandatory fields | Notes |
|---|---|---|
| target | goals[].goal_id, target_variable, setpoint, tolerance | numeric target; `setpoint` value/range |
| observation | observations[].metric_id, source, sampling_interval, freshness_limit | each target variable must have matching metric |
| provenance | ObservationIn.metric_id,value,source_type,ts? | `ai_inference` requires confidence/evidence and cannot be ground truth |
| policy | controller.kind,controller.params, strategy_version | validate with native LoopConfig; no arbitrary code execution |
| schedule | scheduler.mode,interval_seconds,interval_unit | fixed/event/threshold/manual/adaptive/cron |
| adaptive schedule | min_interval_seconds,max_interval_seconds,severity_gain,uncertainty_gain | evaluated from bounded, normalized error; not algorithm adaptation |
| cron schedule | cron_expression,cron_timezone | exactly five fields, IANA timezone |
| actuator | permission_mode, adapter, action_range,max_step | real external adapter needed; high risk => approval |
| verification | delay_cycles, delay_seconds,noise_k | independently observed before/after |

## PID `params`

`kp,ki,kd,derivative_tau,integral_min,integral_max,output_min,output_max,bias,sample_time_seconds`; only SISO Engine.

## MPC `params`

`model:{A,B,C,D?,sample_time}`, `horizon`, `Q`,`R`,`S`,`Qf`,`u_min`,`u_max`,`du_min`,`du_max`,`y_min`,`y_max`,`u_reference`,`max_dt_error_ratio`, `kalman:{Q,R,x0,P0,gate_nis}` optional. Engine supports n states but **m=p=1**. Matrices A=(n,n), B=(n,1), C=(1,n), D=(1,1); Q/R/S/Qf=(1,1). Sample interval must match actual data.

## Adaptive `params`

`expected_gain_sign` MUST be +1 or -1; `trusted_applied_input` default false; `theta0`, `P0`, `forgetting_factor`, `min_samples`, `min_abs_gain`, `max_abs_gain`, `max_abs_a`, `min_input_span`. Actuator *must* define `action_range` and `max_step`. Historical `u` must be independently confirmed **applied** value for the preceding interval; no proposed action or AI inference substitutions.

## Numerical SDK

`StateSpace(A,B,C,D,domain='discrete'|'continuous',sample_time=positive)`.
`DiscreteKalmanFilter(model,Q,R,x,P)`, `ExtendedKalmanFilter(f,h,jac_f,jac_h,Q,R,x,P)` and `UnscentedKalmanFilter(f,h,Q,R,x,P,alpha,beta,kappa)`.
`ConstrainedMPC(model,horizon,Q,R,S,Qf,u_min,u_max,du_min,du_max,y_min,y_max)` and `solve(x0,reference,u_prev)`.
`RecursiveLeastSquares(theta,P,forgetting_factor)` and `SelfTuningARX(rls,expected_gain_sign,...)`.

Refer to the installed FCC source / Pydantic models for precise legal fields and dimensions. The skill is not a substitute for runtime schema validation.
