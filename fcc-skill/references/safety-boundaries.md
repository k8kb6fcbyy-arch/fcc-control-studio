# Operational and safety boundaries for FCC 1.2

- FCC 1.2 is a **local development control framework**, not a safety-certified industrial controller. The provided HTTP server lacks authentication/role authorization: bind to loopback and never expose it directly to an untrusted network.
- Data provenance values `measured`, `human_input`, `ai_inference` are structural declarations, **not cryptographic proofs**. The source may lie. Integrators need real sensor/operator authentication and validated time synchronization.
- A calibrated model is an assumption that must be verified. KF covariance, EKF/UKF state estimates, MPC solutions and self-tuning ARX all rely on model quality. `solve()` success is **not** actual improvement.
- MPC constrained optimizer can fail or produce a plan that is feasible only under the model. The implementation does not prove closed-loop stability, terminal feasibility or robust optimality. Unavailable current actuator value => hold, not assume zero.
- Adaptive ARX needs reliable actual applied u[k] for the preceding interval, not prior proposed actions. Default `trusted_applied_input=false`. Sampling and excitation requirements are not optional; high-risk proposals always require human approval.
- Thread timeouts **do not cancel** a running Python actuator thread. If an external call times out, set `outcome_unknown`, stop retries, reconcile its real state, and use external idempotency/fencing to prevent duplication.
- A rollback method name is not reversible physical reality. Verify actual state, and record failed rollback honestly.
- Native Engine MPC/ARX `set_value` are SISO, while mathematical MPC/KF supports some MIMO. Do not attempt multi-channel actuation through the scalar API by stacking multiple uncoordinated proposals.
- Independent real-world device interlocks, watchdogs, permissions, tenant segregation, backups, audit retention and network security belong to application deployment outside this SDK.
- Scheduler's adaptive interval is **not** adaptive gain/model control. TrendTrigger is an SDK detector not automatically wired to a perpetual event source.
- Auto strategy upgrade, direct generic textual actuation, and production-grade high availability are **not implemented**.
- Action `approved`, `pending_verification` or `outcome_unknown` cannot be reported as success. Obtain independent post-action evidence after actual settling time; otherwise report `inconclusive`.
