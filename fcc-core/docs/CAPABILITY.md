# FCC 1.3 能力台账（底层算法同 1.2）

> `implemented` 表示实现了所述范围且有测试，不代表生产安全认证。数学算法的实现与 FCC 执行链覆盖范围必须分开理解。

| 模块 | 状态 | 来源 | 准确适用范围 |
|---|---|---|---|
| `goal_manager` | `implemented` | `engineered` | 多目标、单点/区间目标、四方向、容差、优先级、目标冲突检测（目标管理是软件概念，经典控制理论无对应） |
| `observation_manager` | `implemented` | `engineered` | 模块本身是工程实现（注册、范围校验、缺失/异常值策略、采样有效期）；只有采样周期这一个参数取自 classical，不把整个管理器记在书里 |
| `comparator` | `implemented` | `classical` | 误差与容差来自基础控制指标；振荡检测（12 轮 ≥3 次符号翻转）仅是工程启发式，不能当作极限环证明 |
| `controller.monitor` | `implemented` | `engineered` | 只观察不动作（软件模式，纯软件操作模式） |
| `controller.threshold` | `implemented` | `engineered` | 开关、死区、迟滞、冷却和步长限制是工程实现，没有提供严格的非线性稳定性证明 |
| `controller.rule` | `implemented` | `engineered` | IF 条件 -> 动作 的声明式规则表（软件规则引擎，非控制理论） |
| `controller.ai_assisted` | `implemented` | `engineered` | 只产出建议且强制带 uncertainty/evidence，动作源恒为 ai_inference（AI 辅助是工程扩展） |
| `controller.human_in_loop` | `implemented` | `engineered` | 产出建议后挂起，等 submit_human_feedback 决策（审批流是工程扩展） |
| `controller.pid` | `implemented` | `classical` | SISO 数字 PID：比例/积分/测量值微分/一阶滤波、积分抗饱和和执行量限幅。使用前必须辨识对象、整定和验证稳定性。 |
| `controller.mpc` | `implemented` | `external:优化控制` | SISO FCC loop + MIMO Python MPC（有限时域、Q/R/S/Qf 权重、u/du/y 约束、SLSQP 求解）。最优性/稳定性/递归可行性均不保证；求解失败不执行。 |
| `controller.state_space_kalman` | `implemented` | `external:随机控制` | 离散线性 KF 的状态预测/测量校正、Joseph 协方差、缺失通道、NIS 门限、状态持久化。可配置为 SISO MPC 内状态估计器；EKF/UKF 为 Python SDK 独立模块。 |
| `controller.adaptive_arx` | `implemented` | `external:自适应控制` | RLS 辨识一阶 SISO ARX 与带符号/参数/激励/幅度/审批约束的自校正建议。需明确 trusted_applied_input，默认仅观察；不能证明稳定性。 |
| `actuator` | `implemented` | `engineered` | 三种权限模式、幂等键、超时、重试、范围/步长限制、回滚方法登记（执行器工程封装） |
| `verification` | `implemented` | `engineered` | 五态判定 + 验证延迟 + 与基线对比 + MAD 噪声带：工程对照实验，不用 AI 自评，不做数学证明 |
| `scheduler` | `implemented` | `engineered` | 调度器本身是工程实现；固定周期属于数字控制的时间尺度，事件/阈值触发为软件工程扩展 |
| `scheduler.adaptive` | `implemented` | `engineered` | 按容差归一化偏差/不确定性调整评估频率，有上/下限；纯调度启发式，不是自适应控制器。 |
| `scheduler.cron` | `implemented` | `engineered` | 支持 5 字段 Cron 的星号、步长、范围、列表，IANA 时区处理，并由 Engine.tick 调度；不支持 Quartz 等扩展语法。 |
| `scheduler.trend` | `implemented` | `engineered` | 滑窗最小二乘斜率事件检测器（Python API）；需外部观测调度接线，Engine.tick 不自动读取新样本触发趋势。 |
| `learning.stats` | `implemented` | `engineered` | 按策略版本统计 N 次干预里 improved/unchanged/worsened/failed/inconclusive 次数 |
| `learning.auto_upgrade` | `not_implemented` | `engineered` | 自动策略升级不做——会污染可审计性，P1 只出统计给人看 |
| `learning.system_identification` | `not_implemented` | `classical` | 系统辨识在经典控制理论 2.13；在线更新对象参数本轮未实现 |
| `learning.daily_digest` | `not_implemented` | `engineered` | 中期闭环的每日汇总编排未实现（编排逻辑，软件工程功能） |
| `audit` | `implemented` | `engineered` | 观测/决策/动作/验证/审批/回滚全链留痕，含 before/after 快照 |
| `theory.state_space` | `implemented` | `classical` | 通用有限维 LTI 状态空间模型，有限矩阵、维度验证及离散状态推进（依赖有效模型） |
| `theory.zoh_discretization` | `implemented` | `classical` | 连续线性系统的零阶保持离散化（增广矩阵指数实现） |
| `theory.linear_stability` | `implemented` | `classical` | 已知线性模型严格特征值稳定条件；非线性/模型未知情形不可套用 |
| `theory.controllability_observability` | `implemented` | `classical` | 基于线性系统控制性/可观测性矩阵秩判定 |
| `theory.state_feedback` | `implemented` | `classical` | 全状态反馈 u=-Kx+v，指定极点需对象可控 |
| `theory.luenberger_observer` | `implemented` | `classical` | 确定性离散观测器及误差动态 A-LC；不是卡尔曼滤波 |
| `theory.first_order_arx` | `implemented` | `classical` | 一阶带常量项经验 ARX 最小二乘辨识（只对应理论系统辨识主题；非自动策略更新） |
| `theory.linear_kalman` | `implemented` | `external:随机控制` | 线性离散 KF，Joseph 更新，NIS 门限、缺失观测通道。 |
| `theory.ekf` | `implemented` | `external:非线性随机估计` | 提供显式 f/h 与 Jacobians 的 EKF；需自己提供可验证模型。 |
| `theory.ukf` | `implemented` | `external:非线性随机估计` | 标准缩放 Sigma 点 UKF、加性噪声、NIS 门限，Python API。 |
| `theory.lqr` | `implemented` | `external:最优控制` | 离散无限时域 LQR（DARE）与闭环极点检查，纯数学 API。 |
| `theory.mpc` | `implemented` | `external:优化控制` | MIMO 有限时域、输入/变化率/输出线性约束的数值优化，返回预测轨迹与首个控制动作。 |
| `theory.rls_adaptation` | `implemented` | `external:自适应控制` | 在线递归最小二乘 RLS、自校正一阶 ARX（仅模型估计与有保护的建议）。 |
| `theory.numerical_linearization` | `implemented` | `classical` | 中心差分数值线性化；局部近似，非全局线性性证明。 |
| `theory.frequency_response` | `implemented` | `classical` | 连续/离散 LTI 频率响应幅相与采样 Nyquist 边界检查；未实现增益相位裕度自动求取。 |
| `theory.lyapunov_certificate` | `implemented` | `classical` | 离散/连续 LTI 正定 Lyapunov 数值证书；不能外推到非线性未知对象。 |
| `theory.transfer_step` | `implemented` | `classical-4` | SISO 状态空间转传函与模型步进仿真。 |
| `web_panel` | `implemented` | `engineered` | I/O 简易面板（单数值指标）+ 原生 HTML/ECharts 专家面板：建 loop、趋势图、时间线、审批、能力灰显 |
