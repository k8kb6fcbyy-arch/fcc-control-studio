# FCC commissioning: required fields, gates, runtime and checkpoints

## Why commissioning exists

A user can describe an outcome without knowing a plant model. The agent obtains verifiable definitions and only then compiles `Intake` into a real `LoopConfig`. **Do not extract numerical gain/plant matrices from prose.** The resulting monitor loop is intentionally less autonomous than the separate numerical Core.

## Actual 1.5 HTTP contract

Start local FCC server with `python -m fcc.server --host 127.0.0.1 --port 8099 --db fcc.db`.

| Stage | Endpoint | Inputs/outputs |
|---|---|---|
| Create empty or partly completed draft | `POST /api/onboarding/plans` | JSON partial `Intake`; response includes id, missing fields and next questions |
| Continue interview | `PATCH /api/onboarding/plans/{id}` | partial JSON; persists draft, returns next missing stage |
| View draft | `GET /api/onboarding/plans/{id}` | `intake`, `required_missing`, `next_questions`, `can_compile` |
| Compile | `GET /api/onboarding/plans/{id}/compile` | validated `loop_config`, warnings and readiness |
| Activate monitoring | `POST /api/onboarding/plans/{id}/activate` | real `loop_id`, enables timed evaluation |
| Submit reading | `POST /api/onboarding/plans/{id}/observations` | `{"value":85,"source_type":"human_input"}` or AI inference with evidence/confidence |
| Monitor | `GET /api/onboarding/plans/{id}/status` | fresh/trusted flag, goal gap, observed success, next suggestion |
| Report/checkpoint | `POST /api/onboarding/plans/{id}/checkpoint` | persists manual report |
| Scheduled inbox | `GET /api/onboarding/plans/{id}/outbox` | persisted reports; GET query `unread_only=true` optional |
| Acknowledge | `POST /api/onboarding/plans/{id}/outbox/{report_id}/ack` | read receipt; not external delivery |
| Suggest action | `POST /api/onboarding/plans/{id}/interventions` | `description` + optional `reason` |
| Confirm real-world application | `POST /api/onboarding/plans/{id}/interventions/{intervention_id}/applied` | `actor` and actual `evidence` required |
| Verify | `POST /api/onboarding/plans/{id}/interventions/{intervention_id}/verify` | wait for delay then independent new observation; observational verdict |
| Pause/resume | `POST /api/onboarding/plans/{id}/pause` or `/resume` | prevents/adds scheduled evaluations and reports |

## Required user-supplied fields

- `title` project label.
- `desired_outcome` text explaining ultimate objective.
- `metric_name` primary numeric metric with `unit` optional.
- `target_value` numeric setpoint; `target_mode` = `exact`, `at_least`, `at_most` (default exact); `tolerance` >= 0.
- `data_source` explicitly `measured`, `human_input`, or `ai_inference`. Never silently change provenance. AI inference needs confidence [0,1] and evidence on each sample.
- `sampling_seconds`, `evaluation_seconds` (must be >= sampling interval), `report_seconds` all positive.
- `action_authority` = `observe` (no candidates), `suggest` (record candidate), `approval_required` (requires named lever). All compile to Core monitor/suggest_only; real actuator permission is a different build/integration step.

## Optional, but strongly recommended

`claimed_baseline` (unverified and only informative), `controllable_levers`, `constraints`, `side_effect_metrics`, `horizon_days`, `success_definition`, `verification_delay_seconds`, `max_data_age_seconds`, `sustained_samples` default 3, `data_owner`, `data_delivery` (`manual`/`push`/`registered_adapter`), `adapter_name` if registered. `report_channel` currently only `local_inbox`.

## Hard gates

- Missing primary target / measurement / source / timings / permission => **draft**, not runnable.
- Target is quantified but no real adapter => run monitor only with manual/API push data, not claim live autonomous acquisition.
- Source is AI-only => no automatic success claim or intervention on AI-only evidence.
- Multiple outcome metrics or constrained vector dynamics => do not fake SISO completeness; build multiple loops with dependency/risk checks or use expert multi-output SDK.
- Observation freshness and spacing matter. Three rapid identical submissions do not prove stability.
- User-described controllable lever is a label, **not a validated actuator mapping**.
- Proposed / applied / verified are distinct states, and verification only checks before-after association.
- Local checkpoint reports require background FCC service to stay running; reports go to SQLite inbox and must be polled/delivered by separate approved connector.

## Example interaction

Agent: 你希望达成什么长期结果？
User: 我希望某个指标变好。
Agent: 用什么可以核对的数字衡量？目标应该至少达到、至多还是接近某个数？
User: 指标 A 至少 100，单位 U，允许误差 2。
Agent: 数据谁提供？多久更新？多久评估和汇报？你允许我做建议，还是只能观察？
User: API 每小时推送一次，每小时检查，每天出报告，只建议。
Agent: 将目标转化为 `target_mode=at_least, target_value=100, tolerance=2, sampling_seconds=3600, evaluation_seconds=3600, report_seconds=86400, action_authority=suggest`。现在需要你授权开启并确保 API 数据源实际连接。

This synthetic example is not a sector-specific preset.
