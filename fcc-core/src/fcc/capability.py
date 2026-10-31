"""Capability registry: implemented, interface-only, or unavailable.

Theory labels describe the *type of method*, not a claim of source ownership:
- classical: linear/PID/observer/frequency-domain concepts
- external: advanced numerical branches (optimal/stochastic/adaptive)
- engineered: software integration, permissions, scheduling and audit
"""

from __future__ import annotations

from typing import Literal

Status = Literal["implemented", "interface_only", "not_implemented"]

# 模块 -> (状态, 说明, 理论来源)
CAPABILITIES: dict[str, dict[str, str]] = {
    "commissioning.guided_intake": {
        "status": "implemented", "theory_source": "engineered",
        "note": "通用五阶段访谈，保存部分草案，动态返回缺失问题；不猜测业务测量或物理控制映射。",
    },
    "commissioning.quantification": {
        "status": "implemented", "theory_source": "engineered",
        "note": "从目标/观测/频率/边界编译实际 LoopConfig；初次编译恒为 monitor + suggest_only，不自动生成 PID/MPC 模型。",
    },
    "commissioning.local_checkpoints": {
        "status": "implemented", "theory_source": "engineered",
        "note": "运行服务的调度线程按指定间隔将报告写入 SQLite 本地收件箱，并提供 HTTP 拉取/已读；不包含主动外部投递。",
    },
    "commissioning.intervention_tracking": {
        "status": "implemented", "theory_source": "engineered",
        "note": "记录候选建议/外部实际实施证据/新观测后前后对比；不能独立证明因果或执行权限。",
    },
    "notification.proactive_external": {
        "status": "not_implemented", "theory_source": "engineered",
        "note": "无原生聊天、邮件、短信主动推送。需要经授权的另一个通知适配器或 Agent 轮询收件箱。",
    },
    "commissioning.autonomous_reconfiguration": {
        "status": "not_implemented", "theory_source": "engineered",
        "note": "没有跨业务普适的自动动作映射/自动执行/策略生产环境升级，需独立适配器、实测可控性及安全验证。",
    },
    "goal_manager": {
        "status": "implemented",
        "theory_source": "engineered",
        "note": "多目标、单点/区间目标、四方向、容差、优先级、目标冲突检测（目标管理是软件概念，与数学控制算法分开实现）",
    },
    "observation_manager": {
        "status": "implemented",
        "theory_source": "engineered",
        "note": "模块本身是工程实现（注册、范围校验、缺失/异常值策略、采样有效期）；只有采样周期这一个参数取自 classical，不把整个管理器记在书里",
    },
    "comparator": {
        "status": "implemented",
        "theory_source": "classical",
        "note": "误差与容差来自基础控制指标；振荡检测（12 轮 ≥3 次符号翻转）仅是工程启发式，不能当作极限环证明",
    },
    "controller.monitor": {
        "status": "implemented",
        "theory_source": "engineered",
        "note": "只观察不动作（软件模式，纯软件操作模式）",
    },
    "controller.threshold": {
        "status": "implemented",
        "theory_source": "engineered",
        "note": "开关、死区、迟滞、冷却和步长限制是工程实现，没有提供严格的非线性稳定性证明",
    },
    "controller.rule": {
        "status": "implemented",
        "theory_source": "engineered",
        "note": "IF 条件 -> 动作 的声明式规则表（软件规则引擎，非控制理论）",
    },
    "controller.ai_assisted": {
        "status": "implemented",
        "theory_source": "engineered",
        "note": "只产出建议且强制带 uncertainty/evidence，动作源恒为 ai_inference（AI 辅助是工程扩展）",
    },
    "controller.human_in_loop": {
        "status": "implemented",
        "theory_source": "engineered",
        "note": "产出建议后挂起，等 submit_human_feedback 决策（审批流是工程扩展）",
    },
    "controller.pid": {
        "status": "implemented",
        "theory_source": "classical",
        "note": "SISO 数字 PID：比例/积分/测量值微分/一阶滤波、积分抗饱和和执行量限幅。使用前必须辨识对象、整定和验证稳定性。",
    },
    "controller.mpc": {
        "status": "implemented",
        "theory_source": "external:优化控制",
        "note": "SISO FCC loop + MIMO Python MPC（有限时域、Q/R/S/Qf 权重、u/du/y 约束、SLSQP 求解）。最优性/稳定性/递归可行性均不保证；求解失败不执行。",
    },
    "controller.state_space_kalman": {
        "status": "implemented",
        "theory_source": "external:随机控制",
        "note": "离散线性 KF 的状态预测/测量校正、Joseph 协方差、缺失通道、NIS 门限、状态持久化。可配置为 SISO MPC 内状态估计器；EKF/UKF 为 Python SDK 独立模块。",
    },
    "controller.adaptive_arx": {
        "status": "implemented",
        "theory_source": "external:自适应控制",
        "note": "RLS 辨识一阶 SISO ARX 与带符号/参数/激励/幅度/审批约束的自校正建议。需明确 trusted_applied_input，默认仅观察；不能证明稳定性。",
    },
    "actuator": {
        "status": "implemented",
        "theory_source": "engineered",
        "note": "三种权限模式、幂等键、超时、重试、范围/步长限制、回滚方法登记（执行器工程封装）",
    },
    "verification": {
        "status": "implemented",
        "theory_source": "engineered",
        "note": "五态判定 + 验证延迟 + 与基线对比 + MAD 噪声带：工程对照实验，不用 AI 自评，不做数学证明",
    },
    "scheduler": {
        "status": "implemented",
        "theory_source": "engineered",
        "note": "调度器本身是工程实现；固定周期属于数字控制的时间尺度，事件/阈值触发为软件工程扩展",
    },
    "scheduler.adaptive": {
        "status": "implemented",
        "theory_source": "engineered",
        "note": "按容差归一化偏差/不确定性调整评估频率，有上/下限；纯调度启发式，不是自适应控制器。",
    },
    "scheduler.cron": {
        "status": "implemented",
        "theory_source": "engineered",
        "note": "支持 5 字段 Cron 的星号、步长、范围、列表，IANA 时区处理，并由 Engine.tick 调度；不支持 Quartz 等扩展语法。",
    },
    "scheduler.trend": {
        "status": "implemented",
        "theory_source": "engineered",
        "note": "滑窗最小二乘斜率事件检测器（Python API）；需外部观测调度接线，Engine.tick 不自动读取新样本触发趋势。",
    },
    "learning.stats": {
        "status": "implemented",
        "theory_source": "engineered",
        "note": "按策略版本统计 N 次干预里 improved/unchanged/worsened/failed/inconclusive 次数",
    },
    "learning.auto_upgrade": {
        "status": "not_implemented",
        "theory_source": "engineered",
        "note": "自动策略升级不做——会污染可审计性，P1 只出统计给人看",
    },
    "learning.system_identification": {
        "status": "not_implemented",
        "theory_source": "classical",
        "note": "未实现引擎自动在线模型更新；独立 ARX/RLS 数学接口已实现",
    },
    "learning.daily_digest": {
        "status": "not_implemented",
        "theory_source": "engineered",
        "note": "中期闭环的每日汇总编排未实现（编排逻辑，软件工程功能）",
    },
    "audit": {
        "status": "implemented",
        "theory_source": "engineered",
        "note": "观测/决策/动作/验证/审批/回滚全链留痕，含 before/after 快照",
    },
    "theory.state_space": {
        "status": "implemented",
        "theory_source": "classical",
        "note": "通用有限维 LTI 状态空间模型，有限矩阵、维度验证及离散状态推进（依赖有效模型）",
    },
    "theory.zoh_discretization": {
        "status": "implemented",
        "theory_source": "classical",
        "note": "连续线性系统的零阶保持离散化（增广矩阵指数实现）",
    },
    "theory.linear_stability": {
        "status": "implemented",
        "theory_source": "classical",
        "note": "已知线性模型严格特征值稳定条件；非线性/模型未知情形不可套用",
    },
    "theory.controllability_observability": {
        "status": "implemented",
        "theory_source": "classical",
        "note": "基于线性系统控制性/可观测性矩阵秩判定",
    },
    "theory.state_feedback": {
        "status": "implemented",
        "theory_source": "classical",
        "note": "全状态反馈 u=-Kx+v，指定极点需对象可控",
    },
    "theory.luenberger_observer": {
        "status": "implemented",
        "theory_source": "classical",
        "note": "确定性离散观测器及误差动态 A-LC；不是卡尔曼滤波",
    },
    "theory.first_order_arx": {
        "status": "implemented",
        "theory_source": "classical",
        "note": "一阶带常量项经验 ARX 最小二乘辨识（只对应理论系统辨识主题；非自动策略更新）",
    },
    "theory.linear_kalman": {
        "status": "implemented", "theory_source": "external:随机控制",
        "note": "线性离散 KF，Joseph 更新，NIS 门限、缺失观测通道。",
    },
    "theory.ekf": {
        "status": "implemented", "theory_source": "external:非线性随机估计",
        "note": "提供显式 f/h 与 Jacobians 的 EKF；需自己提供可验证模型。",
    },
    "theory.ukf": {
        "status": "implemented", "theory_source": "external:非线性随机估计",
        "note": "标准缩放 Sigma 点 UKF、加性噪声、NIS 门限，Python API。",
    },
    "theory.lqr": {
        "status": "implemented", "theory_source": "external:最优控制",
        "note": "离散无限时域 LQR（DARE）与闭环极点检查，纯数学 API。",
    },
    "theory.mpc": {
        "status": "implemented", "theory_source": "external:优化控制",
        "note": "MIMO 有限时域、输入/变化率/输出线性约束的数值优化，返回预测轨迹与首个控制动作。",
    },
    "theory.rls_adaptation": {
        "status": "implemented", "theory_source": "external:自适应控制",
        "note": "在线递归最小二乘 RLS、自校正一阶 ARX（仅模型估计与有保护的建议）。",
    },
    "theory.numerical_linearization": {
        "status": "implemented", "theory_source": "classical",
        "note": "中心差分数值线性化；局部近似，非全局线性性证明。",
    },
    "theory.frequency_response": {
        "status": "implemented", "theory_source": "classical",
        "note": "连续/离散 LTI 频率响应幅相与采样 Nyquist 边界检查；未实现增益相位裕度自动求取。",
    },
    "theory.lyapunov_certificate": {
        "status": "implemented", "theory_source": "classical",
        "note": "离散/连续 LTI 正定 Lyapunov 数值证书；不能外推到非线性未知对象。",
    },
    "theory.transfer_step": {
        "status": "implemented", "theory_source": "classical",
        "note": "SISO 状态空间转传函与模型步进仿真。",
    },
    "web_panel": {
        "status": "implemented",
        "theory_source": "engineered",
        "note": "中文引导式目标建模 /setup、I/O 简易面板 /easy 与原生 HTML/ECharts 专家面板 /expert；HTTP 接口无身份认证。",
    },
}

THEORY_SOURCE_LEGEND = {
    "classical": "经典控制理论",
    "external": "不在经典控制理论，属控制理论其它分支",
    "engineered": "纯软件工程扩展，软件工程功能",
}

RED_LINE_MODULES = ("ai_separation_guard",)


def capabilities_payload() -> dict[str, object]:
    counts = {"implemented": 0, "interface_only": 0, "not_implemented": 0}
    for item in CAPABILITIES.values():
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "modules": CAPABILITIES,
        "counts": counts,
        "red_lines": {
            "ai_only_deviation_cannot_intervene": True,
            "ai_action_forced_approval": True,
            "no_success_without_verification": True,
        },
    }


def status_of(module: str) -> Status:
    item = CAPABILITIES.get(module)
    return item["status"] if item else "not_implemented"  # type: ignore[return-value]
