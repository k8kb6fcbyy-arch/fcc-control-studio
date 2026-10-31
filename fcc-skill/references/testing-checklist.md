# Generic acceptance checklist for any control-loop integration

## Minimal correct use

- [ ] Target, output, units and tolerance have a meaningful interpretation; no AI-only guess is labeled as a measurement.
- [ ] Actual applied inputs (not requested inputs) are recorded with timestamps.
- [ ] Model dimensionality and sample_time match measurements.
- [ ] Initialization starts with a defined prior and does not predict a nonexistent time step.
- [ ] Input/output bounds, max_step, sampling interval and delayed plant response are considered.
- [ ] Model prediction versus independent ground-truth verification is separately reported.
- [ ] Permission gates are effective; high-risk/irreversible actions require approval.
- [ ] Every execution is idempotent, and timeout -> outcome_unknown -> no blind retries.

## Advanced mathematical checks

- [ ] KF Q positive semidefinite, R positive definite, P prior PSD; cross-check residuals and normalized innovation.
- [ ] EKF Jacobians match f/h at real operating points; UKF sigma-point parameters are numerically sensible.
- [ ] MPC optimization respects u, delta-u and y limits under randomized test states; infeasible constraints hold output, not fallback to unbounded control.
- [ ] RLS model is identifiable under sufficient variation in real inputs; unstable/invalid sign/weak gain parameter estimates are rejected.
- [ ] Adaptive policy is tested against out-of-sample behavior and model drift, not merely training residuals.
- [ ] PID/LQR/observers have evaluated closed-loop behavior on a defensible plant model and independent simulator.
- [ ] Frequency response and Lyapunov conclusions mention exact LTI assumptions.

## Production gating

- [ ] Authentication, authorization, secrets, audit retention, isolated tenant DB and backups are designed externally.
- [ ] Physical actuator has interlocks independent of FCC (where applicable).
- [ ] Independent operator can stop/restore loop, reconcile unknown outcomes and inspect evidence.
- [ ] Any changes in sampling, plant, sensor, controller or weights create a versioned change and require revalidation.

## Command checks

```
python -m pip install -e '.[test]'
pytest -q
python examples/advanced_control.py
```

For the 1.2 reference release, all **221** unit/integration tests passed in the supplied environment. This is *software testing*, not guaranteed closed-loop plant safety.
