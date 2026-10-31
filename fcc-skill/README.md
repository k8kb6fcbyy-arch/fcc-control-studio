# FCC Feedback Orchestrator SKILL 1.5

Load this folder in an Agent Skills-compatible AI host. The required file is `SKILL.md`. It instructs AI to interview users progressively, quantify a measurable goal, set separate sampling/evaluation/report clocks, identify controllable actions and permissions, compile a live FCC Core config, and retrieve checkpoint reports.

**Before adopting the workflow, check suitability:** [Use cases & limitations (English)](../docs/USE-CASES-AND-LIMITATIONS.en.md) · [适用场景与局限性（中文）](../docs/USE-CASES-AND-LIMITATIONS.zh-CN.md). A numeric goal alone does not establish control authority, causal validity or safe automation.

**Core must be installed separately.** A SKILL is instructions + a local CLI; it does not grant the host network/process/file permissions, does not keep jobs running after a chat ends, and cannot send proactive messages. Persistent scheduling requires a running FCC Core server and working data feed; external delivery requires a separate approved adapter.

**First step:** determine the host capabilities. From the repository root with both folders:

```bash
python fcc-skill/scripts/fcc_bootstrap.py doctor --json
```

For a chat-only AI, simply load `SKILL.md` and answer its questions. Do not claim the system is installed or scheduling reports. For a local coding agent, use the explicit install/start commands documented in `references/bootstrap.md` only after permission.

Basic CLI (from this directory):

```bash
python scripts/fcc_agent.py health
python scripts/fcc_agent.py create --json '{}'
python scripts/fcc_agent.py answer --plan-id plan_xxx --file templates/intake-template.json
python scripts/fcc_agent.py compile --plan-id plan_xxx
python scripts/fcc_agent.py activate --plan-id plan_xxx
python scripts/fcc_agent.py status --plan-id plan_xxx
python scripts/fcc_agent.py inbox --plan-id plan_xxx
```

Read `references/onboarding.md` for the actual HTTP endpoints and sample payloads.
