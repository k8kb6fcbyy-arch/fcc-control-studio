# WorkBuddy / Coding Agent: FCC Core + Universal SKILL 1.5 handoff

1. Load `SKILL.md` as procedural spec and `references/onboarding.md` as the actual API contract; avoid assuming an AI host runs tasks in the background.
2. Confirm FCC Core 1.5 service is installed and alive at `127.0.0.1:8099`, and check the server's data adapters. Never expose its unauthenticated demo API publicly.
3. **Begin with a five-stage interactive intake**, ask only the earliest stage of unanswered questions; preserve user answers in `/api/onboarding/plans/{id}`. Do not make up a sensor, baseline, unit, control direction or model.
4. Verify target semantics, valid data source, and sampling/evaluation/report intervals. Ask about controllable levers, side effects, action authority and response delay before promising improvement.
5. Compile with `/compile`, present assumptions and safety gates to the user, then activate only with permission. Core uses monitor mode by default. This is not an arbitrary numeric PID/MPC generator.
6. Configure an authorized process/host to keep Core alive and an actual manual/API/adapter feed to deliver new observations. Scheduled local inbox entries are not automatic ChatGPT/WorkBuddy replies.
7. At checkpoints use `/status` and `/outbox`. Recommendations are hypotheses. Record explicit proposed intervention, separately collect external actor/evidence that it was applied, require fresh readback after response time, then verify.
8. For numerical model-based control consult `references/advanced-control.md`; do not bypass safety to make a demo look autonomous. Maintain all tests and provide failure/uncertainty evidence in reports.
