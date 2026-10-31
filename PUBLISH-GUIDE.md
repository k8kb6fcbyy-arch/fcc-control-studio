# GitHub publication checklist / 发布方法

**Repository name suggestion:** `fcc-control-studio`  
**GitHub display name:** Chans  
**Commit email:** chenchen_wang@foxmail.com (optional; GitHub also offers a no-reply email)

The repo metadata in GitHub Settings → General → Description can be:

> A domain-neutral control and feedback engine with an agent Skill for onboarding, measurable goals, monitoring and verification. By Chans.

Publish by creating an **empty** repository on GitHub (do not add a different LICENSE), then from the root of this folder run:

```bash
git init
git branch -M main
git config user.name "Chans"
git config user.email "chenchen_wang@foxmail.com"
git add .
git status --short
git commit -m "Initial public source release"
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/fcc-control-studio.git
git push -u origin main
```

Replace `YOUR_GITHUB_USERNAME` with your real account. These commands configure **this repository** only; they do not modify global Git identity. Your GitHub username and commit author do not by themselves enforce third-party attribution. `LICENSE` is the operative permission document.

Before making it public:
1. Confirm no third-party or employer-owned proprietary code is included without permission.
2. Run `python -m pytest -q` in `fcc-core`.
3. Review `git status` for passwords, `.env`, SQLite databases, private files, contact data and huge binaries.
4. Read the custom license and consider qualified legal review: it is not OSI-approved and its invocation condition may affect third-party adoption.
5. Do not represent the application as production-secure or autonomously notifying external users.
