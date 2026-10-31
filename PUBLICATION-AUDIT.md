# FCC Control Studio — GitHub publication audit (2026-10-31)

**Project maintainer:** Chans · **Contact:** chenchen_wang@foxmail.com

## Release check

- Upgraded from FCC 1.4 to FCC 1.5; preserved existing control library and added AI Bootstrap.
- Test suite: **251 passed** in the available Python environment; test suite does not represent industrial qualification.
- JavaScript: syntax check passed for the new Bootstrap script; the earlier three UI scripts were checked in the previous publication audit.
- Built Python wheel successfully; verified inclusion of new Bootstrap HTML and Python module.
- Included: `fcc-core/`, `fcc-skill/`, first-use guide, release notes, documentation, GitHub Actions test workflow, custom license and origin instructions.
- Excluded: development caches, SQLite DB/WAL, temporary files, compiled artifacts, old development log records.
- No obvious credential/private-key patterns in release sources during targeted text scanning (not equivalent to comprehensive independent secret scanning).
- Removed named-book references from published source and documentation as requested. Mathematical methods and actual capability boundaries are still described.

## FCC 1.5 additions

- Added universal first-use `/start` browser page and `/api/bootstrap` factual runtime status endpoint.
- Added zero-dependency `fcc_bootstrap.py doctor --json` capability preflight, with localhost-only HTTP access.
- Added explicitly authorized local `install --yes` and foreground `serve --yes`. No hidden background agent, remote server or automatic notification is provisioned.
- SKILL now specifies chat-only fallback, host-permission uncertainty, staged interview, data-feed verification and startup consent.
- Added 11 automated tests for security boundary, capability truthfulness, standalone SKILL degradation, API and first-use UI.
- Restored public contact email to `chenchen_wang@foxmail.com`, while keeping maintainer attribution **Chans**.

## Changes

1. Fixed unescaped dynamic values in the expert UI to reduce injection risk.
2. Local-server CLI rejects non-loopback binding; Host validation rejects unknown domains.
3. Cross-site browser write requests rejected; sensitive API responses marked `Cache-Control: no-store`.
4. API and UI expose source attribution; AI Skill includes required attribution instructions.
5. Added tests for Host validation, cross-origin writes, local binding, UI escaping and source provenance.
6. Added repository author, security disclosure, contribution policy, attribution and license documents.
7. Added dependency major-version bounds; these are not pinned exact versions and do not constitute an audit of each installed dependency version.

## Remaining limitations / blockers for production deployment

- **No authentication, authorization, multi-tenant isolation or TLS.** Must remain local-only; do not publish this HTTP service on the internet or a shared LAN.
- **No independent security audit or supply-chain scan completed.** Review transitive dependencies and license compatibility before deployment.
- Expert UI currently loads ECharts JavaScript from external `cdn.jsdelivr.net`; local/offline use requires a separately reviewed and licensed asset.
- Agent Skill files are instructions; installing a Skill does not grant process execution, data access or push notification abilities.
- Active reports are saved in a local inbox, not proactively delivered via chat/mail.
- Numerical controllers and estimators require validated process models. Full MIMO actuator wiring, certified stability/safety and automatic safe policy upgrades are not supplied by this prototype.
- Custom source-available attribution license is **not OSI-approved**. Enforceability, rights chain and suitability for international or commercial distribution require qualified legal review. The author should review and approve the terms before publishing.
- Repository authorship fields do not prove ownership of third-party or AI-assisted portions. Confirm that upstream code and assets can lawfully be released under the chosen terms.

## Attribution format

`Powered by FCC Control Studio — Chans (chenchen_wang@foxmail.com)`

See `LICENSE`, `NOTICE`, `ATTRIBUTION.md`, and `PUBLISH-GUIDE.md`.
