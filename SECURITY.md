# Security policy / 安全使用范围

This repository is a research/development framework. Its HTTP endpoints have **no authentication**, user roles or tenant separation.

- Run the server **only on loopback** (`127.0.0.1` / `localhost`). The CLI refuses non-loopback binding and the HTTP application rejects unexpected Host headers.
- Do **not** place the server directly behind a public domain, reverse proxy, or shared office LAN without independently implementing authentication, authorization, TLS, additional CSRF/origin controls, rate limits, quotas and audit review.
- Keep the SQLite database private. It can contain user goals, observations, workflow history and sensitive project metadata; never commit it.
- Do not attach real-world high-risk actuators without independent hardware interlocks, limits, model calibration, supervision and rollback.
- Some capabilities are algorithmic SDKs, not independently validated production control loops.
- Security issues: contact chenchen_wang@foxmail.com; avoid filing public issues containing sensitive credentials or data.

**Boundaries:** Local Host checks and escaping reduce risk but do not turn the service into a secure multi-user system; local malware or a compromised browser remains a threat.

## Optional third-party web asset

The expert UI (`/expert`) currently loads an ECharts script from `cdn.jsdelivr.net`.
This requires internet connectivity and exposes normal web-request metadata to
that CDN. Do not use the expert UI in a strictly offline/privacy-sensitive
installation without first replacing the external asset with a locally reviewed,
properly licensed copy and adjusting the content security policy. The main
commissioning UI and local API do not require this chart library.

The built-in service also rejects browser cross-origin writes and adds no-store headers; these are defense-in-depth, **not** authentication.
