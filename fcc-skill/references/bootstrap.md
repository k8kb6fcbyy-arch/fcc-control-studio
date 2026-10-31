# AI Bootstrap · FCC 1.5

## Purpose

This protocol is **host-independent and domain-neutral**. AI hosts vary: some can only read an uploaded file, some can execute local commands, and some have durable execution and connectors. A SKILL is procedural guidance, not the power to spawn workers or deliver push messages.

## Start sequence

1. Read `SKILL.md`, preserving known user answers and keeping attribution intact.
2. Check whether the AI host has actually been granted filesystem access, command execution, localhost HTTP access, persistent runtime, and notification abilities. Do not infer these from the brand/name of the AI.
3. If local command execution exists, run from repository root: `python fcc-skill/scripts/fcc_bootstrap.py doctor --json`.
4. Parse `mode`:
   - `conversation_only`: conduct interview, provide human-readable plan and manual follow-up process. No long-running promises.
   - `local_setup_available`: full Core files available and Python compatible, but Core not running. Get authorization **before** `install --yes` and `serve --yes`.
   - `local_scheduler_available`: scheduler available, but still verify a real incoming observation source, actual new data and report pickup.
   - `local_service_without_scheduler`: enable a real scheduler before promising automated checkpoints.
   - `service_mismatch`: do not send actions to an unknown service.
5. After local service startup, open `/start` and `/setup`; run five-stage adaptive intake: outcome, metric, data, clocks, controllable actions and safety.
6. Compile plan and show what is inferred vs user-confirmed, required connector, source type and any missing data.
7. Activate only on explicit authorization; report means **stored locally** unless a real delivery connector was configured and verified.
8. At each checkpoint, examine data freshness and original evidence. Track whether proposed changes were actually performed before any effect comparison. Never promise achievement.

## Authorized operations

These operations require *independent, explicit permission*:

```bash
python fcc-skill/scripts/fcc_bootstrap.py install --yes
python fcc-skill/scripts/fcc_bootstrap.py serve --yes
```

- `doctor` reads environment details and tries only a loopback HTTP health check.
- `install` creates `fcc-core/.venv` and installs Python dependencies (network may be needed), **does not launch** Core.
- `serve` starts Core in the foreground on `127.0.0.1`, with the scheduler enabled. When this process stops, scheduling stops.
- Only listen on localhost. Never proxy the unauthenticated demo API to a public address, embed credentials in a user prompt, or claim production safety.
- Neither command configures an actuator, registers a data adapter, accesses personal online accounts or grants autonomous execution authority.

## AI-only usage example

User uploads only `fcc-skill/` to a chat AI. The AI should say: "我可以先收集目标和反馈指标，然后形成手动复盘计划；目前没有可验证的后台执行环境。我先问你：你最希望改善的结果是什么？" The AI must not say "已为你开启每天自动汇报" unless a separate supported scheduler and delivery mechanism is really configured.

## Attribution

FCC Control Studio — Chans (chenchen_wang@foxmail.com). Follow repository `LICENSE` for integration/reuse.
