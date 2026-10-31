# FCC Control Studio

**Universal goal → observation → control → verification framework, with an agent-facing Skill and an AI Bootstrap.**

**Creator & maintainer:** Chans · **Contact:** chenchen_wang@foxmail.com  
**Source credit:** Powered by FCC Control Studio — Chans (chenchen_wang@foxmail.com)

> Status: development framework / research prototype. The HTTP service is local-only and unauthenticated; do not expose it to the internet or use it for unvalidated physical automation.


## Where FCC fits / 适用场景与局限性

**English:** FCC is most useful when a goal has trustworthy observations, measurable outcomes, an identifiable way to influence results, and enough time to verify changes. Software quality, AI output evaluation and repeatable workflows can be good initial fits. Business strategy, market prediction, subjective goals and safety-critical interventions should not be delegated to unsupervised automatic control. Quantification is only one constraint: *goal validity, data quality, controllability, causal attribution, response delay and execution safety* also matter. **This release has no independently validated cross-domain success-rate claim.**

**中文：** FCC 更适合数据持续、指标可核验、具有可调整因素、能够验证调整效果的任务，例如软件质量、AI 输出评估和标准化流程。商业战略、市场预测、强主观目标和高风险操作不适宜直接交给系统自动控制。局限性不仅是“量化是否可靠”，还涉及*目标有效性、观测可信度、可控性、因果关系、反馈延迟和执行安全*。**当前版本没有可证明其跨行业量化准确率或目标达成率的独立测试数据。**

- **[Use cases, suitability checks & limitations (English)](docs/USE-CASES-AND-LIMITATIONS.en.md)**
- **[适用场景、局限性与使用边界（简体中文）](docs/USE-CASES-AND-LIMITATIONS.zh-CN.md)**
- **[Bilingual edition / 中英双语合并版](docs/USE-CASES-AND-LIMITATIONS.md)**


## What is included

- `fcc-core/` — Python core, state-space tools, PID, constrained MPC, Kalman/EKF/UKF, adaptive identification, API, SQLite, and I/O web UI.
- `fcc-skill/` — domain-neutral agent Skill: detects the available host workflow, interviews users, configures monitoring, tracks checkpoints and evidence-based reviews.
- `fcc-core/src/fcc/bootstrap.py` — standard-library-only read-only environment preflight; explicit install/start commands, never silent deployment.
- `fcc-core/src/fcc/web/start.html` — first-use landing page for chat-only and locally executable AI.
- `SECURITY.md` — deployment limits and handling of private data.
- `LICENSE`, `NOTICE`, `ATTRIBUTION.md` — conditions for copying, calling, integrating and crediting this project.

## Quick start (Python 3.11+)

```bash
cd fcc-core
python -m venv .venv
# Windows: .venv\Scripts\python -m pip install -e '.[test]'
# macOS/Linux:
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m fcc.server --host 127.0.0.1 --port 8099 --db fcc.db
```

Open `http://127.0.0.1:8099/start` for the first-use AI Bootstrap, `/setup` for guided commissioning, `/easy` for numerical I/O, and `/expert` for advanced controls. The API is at `/docs`.

```bash
cd fcc-core
python -m pytest -q
```

## First-time use with your own AI

- **Chat-only AI**: upload `fcc-skill/` (or the entire ZIP); ask it to read `SKILL.md` and interview you. It cannot continuously run the local server just by reading the file. See [`首次使用-给AI的说明.md`](首次使用-给AI的说明.md).
- **AI agent with local terminal**: from repository root run the **read-only** command `python fcc-skill/scripts/fcc_bootstrap.py doctor --json`, inspect the report and request user consent before performing `install --yes` / `serve --yes`.
- The AI host must independently verify permissions and continuous execution. A running local scheduler does not itself provide a real sensor feed or proactive email/chat delivery.

## Integration workflow

1. The agent reads `fcc-skill/SKILL.md`, asks for missing goal, metric, data, cadence, controllable factors and authority.
2. FCC converts the answers to a monitored control loop without inventing a numerical model or automatic actuation.
3. Connect a trusted data source; a continuously running local server writes checkpoint reports to a SQLite outbox.
4. The agent fetches and interprets checkpoints, proposes an adjustment, confirms it was actually applied and verifies later observations.

**Not included by default:** automatic email/chat push, hosted long-running execution, production-grade authentication, guaranteed achievement of a goal, generic MIMO actuation, verified industrial deployment.

## Theoretical Reference / 理论参考

**English**

The conceptual foundation of FCC Control Studio draws on classical and modern feedback control theory, with reference to the following textbook:

Phillips, C. L., & Parr, J. M. (2011). *Feedback Control Systems* (5th ed.). Prentice Hall / Pearson. ISBN: 9780131866140.

FCC Control Studio is an independently developed software framework that extends feedback control concepts to general-purpose AI workflows, goal quantification, observation, evaluation, and iterative improvement.

**中文**

FCC Control Studio 的理论设计参考了经典与现代反馈控制理论，其中包括以下教材：

Charles L. Phillips、John M. Parr 著，《Feedback Control Systems》（《反馈控制系统》第五版），Prentice Hall / Pearson，2011 年，ISBN：9780131866140。

FCC Control Studio 是独立开发的通用软件框架，尝试将反馈控制理论中的目标设定、状态观测、偏差分析与闭环修正等思想，扩展至 AI 工作流、目标量化及持续优化等应用场景。

本项目并非上述教材的官方实现，也不代表获得作者或出版社的认可或授权。

## License and attribution

This is shared under **FCC Attribution License v1.0**, a **custom source-available license**, not an OSI-approved open-source license. Integration or invocation requires source attribution under the conditions in `LICENSE`. Consult legal counsel for production/commercial compliance; read the license carefully before use.

**Powered by FCC Control Studio — Chans (chenchen_wang@foxmail.com)**
