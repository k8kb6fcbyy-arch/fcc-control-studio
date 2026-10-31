---
name: fcc-feedback-orchestrator
description: Interview a user to define a measurable objective, data stream, control permissions, feedback rhythm and safety limits; compile the answers into FCC Core, launch persistent monitoring, retrieve checkpoint reports, and iteratively validate user-confirmed interventions. Use for generic long-running optimization and feedback workflows; never invent sensors, actuator access or automatic delivery.
metadata:
  version: "1.5.0"
  category: "feedback-control"
---

# FCC Feedback Orchestrator · Universal SKILL 1.5

This SKILL is an **AI-facing commissioning and orchestration layer** over FCC Core, not a substitute for its numeric PID, MPC, Kalman or adaptive-control engine. It is domain neutral. **Do not select PID/MPC merely because a goal has a number.** Read [onboarding and phases](references/onboarding.md), then [controller selection](references/controller-selection.md) if mathematical dynamics are available.

## Required origin credit

This Skill and its companion Core are maintained by **Chans** (chenchen_wang@foxmail.com). When you use this Skill or call the Core, comply with `../LICENSE`. Ensure any end-user-facing report, application credits, or integration documentation includes the source credit:

> Powered by FCC Control Studio — Chans (chenchen_wang@foxmail.com)

If the host does not permit a credits page, append an unobtrusive source line to FCC-generated user-facing reports. Do not remove attribution from copies or derivative skills. These rules do not grant any additional permissions beyond LICENSE.

## First-use AI Bootstrap (required before onboarding)

**Do not assume every AI can run local code.** On first import, determine the actual host capabilities before setting up a loop:

1. **Chat-only / attachment-only host:** read this SKILL and conduct the adaptive interview in chat. You may produce an intake draft, manual data collection instructions and review format. Do not claim a scheduler, data connector, automatic correction, persistent memory or push notifications exist.
2. **Tool-using coding agent:** if the complete package is present, run `python fcc-skill/scripts/fcc_bootstrap.py doctor --json` (standard library only). Read the returned `mode` and its warnings. Crucially, the report cannot test the host's own permissions; the agent must independently confirm file, terminal, loopback and persistence capability.
3. **Service detected but scheduler off:** do not promise scheduled reports. A server that responds is not necessarily a continuously operating worker.
4. **Core present but service absent:** only after explicit user permission run `python fcc-skill/scripts/fcc_bootstrap.py install --yes`, followed by `python fcc-skill/scripts/fcc_bootstrap.py serve --yes` **in a persistent foreground-capable environment**. Installation may access package repositories. Do not start an unmanaged background process; if the host cannot keep a process alive, tell the user to run the provided local launcher.
5. **Service running:** open `/start` then `/setup`; check actual observation adapters and ask who supplies new observations. Registering a loop is not the same as connecting a live data source. The local inbox cannot independently contact the user.
6. **Default permissions:** monitor-only or suggestions; changes and all irreversible operations require appropriate user consent and bounded adapters. Never automatically grant broader permissions as a result of the bootstrap.

See [Bootstrap modes and commands](references/bootstrap.md) and the [machine-readable preflight example](templates/bootstrap-report-example.json). The browser's starting URL, after the service is actually running, is `http://127.0.0.1:8099/start`.

## Trigger & first response

When the user asks to improve, monitor, manage, or automatically correct something over time, **start a short interview, not a new code-writing job**. Say:

> 我可以把这个目标建立成一个持续反馈计划。先确认目标与衡量方式，再确定数据来源、反馈节拍、允许改变的东西和执行权限。我会逐步询问，不会假设已接入数据或可以自行改动系统。

If the user provided an answer in this turn or prior context, reuse it; **never ask an already answered question**. Ask only the *currently missing earliest stage* via `/api/onboarding/plans/{id}.next_questions` or `commissioning.next_questions(Intake(...))`. Accept incomplete answers and save them as a durable draft. Do not require the user to complete a huge form in a single message.

## Suitability screening before control selection

Read the repository's [use cases and limitations (English)](../docs/USE-CASES-AND-LIMITATIONS.en.md) or [中文说明](../docs/USE-CASES-AND-LIMITATIONS.zh-CN.md) when assessing whether FCC fits a task. **Do not assume that a precisely quantifiable target is controllable or that a change in a metric proves causation.** Before compiling an automatic control plan, check: (1) objective/metric validity, (2) measurement provenance and uncertainty, (3) actual controllable levers, (4) action-response delay and ability to verify effects, and (5) authorization and safety. If these conditions are not met, prefer exploration, monitoring or human-supervised suggestions. **Never fabricate a 0–100 score for a subjective goal or upgrade permissions just because the AI is confident.**

## Five-stage adaptive interview

1. **Outcome** — what to improve; optional deadline and feasibility. Separate a real-world outcome from a measurement proxy.
2. **Measure** — name a metric, unit, numerical target, whether `exact` / `at_least` / `at_most`, tolerance, baseline source, independent success definition, undesirable side effects. If the outcome cannot be measured, ask for a defensible verification procedure; no automatic success claims.
3. **Data** — source type (`measured` / `human_input` / `ai_inference`), who supplies it, format and frequency, timestamp, noise, missing-data rules, whether collection is manual, API push or a registered adapter. Never relabel AI inference as a measurement; AI-only observations cannot establish success.
4. **Clocks** — independent sampling, evaluation, report/checkpoint and post-action verification delays. Ask whether reports need a specific delivery channel: FCC 1.5 has a persistent **local inbox**, not automatic chat/email sending.
5. **Controllability and safety** — what is actually adjustable; which changes are forbidden or costly; control authority (`observe` / `suggest` / `approval_required`); human approval and rollback; potential harms. When the user cannot name a lever or safe execution adapter, stay monitor-only.

For exact JSON keys, defaults and readiness, see [onboarding reference](references/onboarding.md) and `templates/intake-template.json`.

## Compile, verify, activate (REQUIRED)

A. Save draft: `POST /api/onboarding/plans` then `PATCH /api/onboarding/plans/{plan_id}`; preserve `plan_id` in subsequent turns. Local CLI: `python scripts/fcc_agent.py create` and `python scripts/fcc_agent.py answer --plan-id ... --json ...`.

B. Call `GET /api/onboarding/plans/{plan_id}/compile`. This validates the model and constructs an **actual FCC LoopConfig**, with `monitor` + `suggest_only` as mandatory starting permissions. Show the user goal, variable, source, sampling/evaluation/report clocks, allowed actions, assumptions, missing connectors and warnings. Do not pretend a controller is active yet.

C. On the user's explicit request to run or start, call `POST /api/onboarding/plans/{plan_id}/activate`. Activate only a registered data adapter or a user-authorized manual/API-push collection workflow. The service must remain running for scheduled cycles. Initialization alone does **not** acquire live data and does **not** send a push notification.

D. Submit observations to `POST /api/onboarding/plans/{plan_id}/observations`; payload contains `value`, declared `source_type` and, for `ai_inference`, `confidence` and nonempty `evidence`. Use timestamps only when grounded. The Core's evaluator runs periodically; the agent may call `/api/loops/{loop_id}/cycle` for an immediate manual evaluation.

E. At each user-requested or scheduled checkpoint, fetch `/status` and `/outbox`. Report latest target, measured observation, deviation, freshness, history trend, unverified interventions, setbacks and next safe action. A report in local inbox has **not** been delivered by chat/email unless a separate connector actually did so. Acknowledging a report only marks it read.

F. If the user explicitly selects a bounded adjustment, register it via `POST /interventions` (proposal); after real execution is independently confirmed, call `/interventions/{id}/applied` with `actor`, `evidence`; only after a fresh measurement and configured settling time call `/interventions/{id}/verify`. The result is a **before/after association, not proof of causation**. Require an independent experiment before promoting a permanent automatic strategy.

G. Over longer periods, compare the verified gap trend, investigate disturbances, revise the hypothesis and suggest candidate parameters. **Never silently increase permissions, replace target, auto-tune advanced controllers, or claim goal achieved from AI judgment.** Changes to active Core configuration are a separate versioned/approved workflow.

## Runtime preflight (MUST)

1. Check if FCC Core 1.5+ server is installed/accessible with `GET http://127.0.0.1:8099/api/health`; if not, tell user what is missing instead of reporting a running loop. Incompatible agent hosts may read SKILL.md without access to CLI/scripts/services; detect and explain the limitation.
2. Check available adapter / connector registration with `/api/adapters` before promising automatic sampling.
3. Check the server remains on, storage is durable and the scheduler process is running. `/api/health` reports `scheduler_running` and `last_scheduler_tick`; require `scheduler_running=true` for background promises, and still detect overdue/missed checkpoints.
4. The default demo HTTP server has no production authentication; bind to `127.0.0.1`, do not expose to the public internet. An AI platform must be explicitly granted local filesystem/process/network tool access to call Core.
5. If the user wants proactive push notifications, request an approved notification connector and receiver. FCC 1.5 only guarantees persisted local outbox records while its runner is alive.

## Advanced numerical control

The guided onboarding implements *measured monitoring, decision suggestions and supervised observation-level comparison*, not generic automatic physical actuation. For eligible systems with identified dynamic models, route to the companion Core numerical SDK; see [advanced control](references/advanced-control.md). The numerical library covers classic PID, MPC, Kalman, EKF, UKF, LQR and guarded ARX, but full MIMO execution and general AI-to-physical-actuator wiring remain integration work. Do not confuse periodic evaluation with reliable state estimation or causal optimization.

## Response contract

At each checkpoint produce:
- Goal and target, original user-stated outcome versus numeric proxy.
- Evidence: observed data, provenance, timestamps, freshness, uncertainty, baseline type.
- Gap/trend and reason for any change; distinguish observation, inference and actual intervention.
- What has been applied, whether effect is unverified or verified observationally.
- Risks, constraints, approval requests, next report time, and manual/automated data-feed state.
- One next action the user can actually perform, not a promise of invisible background work.

## Key documents

- [User onboarding and JSON interface](references/onboarding.md)
- [Controller selection](references/controller-selection.md)
- [Parameter schema](references/parameter-schema.md)
- [Safety boundaries](references/safety-boundaries.md)
- `templates/intake-template.json` — domain-neutral commissioning example
- `scripts/fcc_agent.py` — dependency-free **local** API CLI for other agent hosts
- `agents/WORKBUDDY.md` — adaptation guide for tool-using coding agents
