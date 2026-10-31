# FCC Control Studio 1.5 · 发布说明

维护者：**Chans** · 邮箱：**chenchen_wang@foxmail.com**

## 新增

- `/start`：中文 AI 使用方式与本地运行状态入口，区分只有聊天 AI 与可执行代码的 Agent。
- `/api/bootstrap`：输出真实的服务/调度状态、可用观测适配器、本地报告投递边界。
- `fcc-core/src/fcc/bootstrap.py`：仅用 Python 标准库进行本机环境预检，只有明确命令 `install --yes`、`serve --yes` 才会部署或启动。
- `fcc-skill/scripts/fcc_bootstrap.py`：Agent 一条命令进行 `doctor --json` 检测；当仅提供 SKILL 而没有 Core 时能安全降级。
- `fcc-skill/references/bootstrap.md`：AI 宿主能力识别、权限与接入、用户授权、访谈、运行和报告规则。
- `首次使用-给AI的说明.md`：面向普通用户与具备终端权限的 AI 的完整使用说明。

## 验证结果

- 251 项自动化测试全部通过（其中 11 项新增专项测试）。
- 本地实际启动服务、访问 `/start`、检查调度状态、执行 Agent 检测并解析 JSON 已通过。
- Bootstrap 网页 JavaScript 语法检查及 Python 源码编译检查通过。
- Python wheel 构建成功，验证包含新增的网页与 Bootstrap 源码。
- 公开文件中不含此前私密转发邮箱及指定教材名称。

## 限制

- 仅向 AI 上传文件不会自动启动服务、周期任务或主动通知。是否能够部署取决于宿主权限。
- 本地调度需要进程持续运行，数据需要实际接入，检查点默认仅保存在本地收件箱。
- 本地 HTTP API 未实现生产级认证，不能直接暴露公网或用于未经验证的物理设备自主控制。
- 现有复杂多变量执行与自动验证仍存在能力边界，详见 `fcc-core/docs/CAPABILITY.md`。
- 自定义署名许可证属于 source-available，并非标准 OSI 开源许可证；公开前应审查其合法性和代码权属。
