# FCC Control Studio 1.5 · 使用指南

## 只有自己的聊天 AI

将 `fcc-skill` 文件夹（如果 AI 支持文件夹读取）或完整 ZIP 上传给它，让它阅读 `fcc-skill/SKILL.md`。然后发送：

> 请按 FCC SKILL 引导我建立通用持续反馈计划。先检查你的实际能力。如果无法执行本地代码或持续后台运行，就采用对话模式，逐步问我目标、衡量指标、数据来源、反馈节拍和可调整因素，不要声称已经开启定时检查或主动通知。已有信息不要重复询问。

聊天 AI 能帮助你定义指标和复盘方案，但仅上传文件无法保证自动收集数据或后台执行。

## 有终端权限的 AI Agent

将整个项目目录交给 Agent，发送：

> 请先阅读 `fcc-skill/SKILL.md` 和 `fcc-skill/references/bootstrap.md`，执行只读检测：`python fcc-skill/scripts/fcc_bootstrap.py doctor --json`。先报告环境检测结果及未知权限，未经许可不要安装或启动。随后帮助我打开 `/start`，逐步采集控制计划，并核查是否有真实观测数据和持续运行条件。

得到你的明确同意后，Agent 才可以执行：

```bash
python fcc-skill/scripts/fcc_bootstrap.py install --yes
python fcc-skill/scripts/fcc_bootstrap.py serve --yes
```

本机打开 `http://127.0.0.1:8099/start`。如果不能使用 AI Agent，仍可以直接进入 `fcc-core/` 双击 Windows 启动脚本，或在 Mac/Linux 使用 `bash 启动FCC-Mac-Linux.sh`（需要 Python 3.11+ 与首次安装网络）。

## 重要的真实限制

- 安装脚本只在本机部署，不提供云端托管。
- 调度器要常驻服务进程才运行；笔记本睡眠/关闭进程可能中断。
- 报告默认存在本地数据库收件箱，不代表已通过聊天或邮件送达。
- 需要额外配置可信数据输入，AI 才能持续基于新证据校正。
- 必须授权具体控制动作；默认不能自行修改真实设备、资金、外部系统。
- 本地服务无生产级鉴权，只允许绑定本机回环地址，不可直接暴露公网。

作者与来源：FCC Control Studio — Chans (chenchen_wang@foxmail.com)。
