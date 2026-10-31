# 通用 I/O 输出格式 (Agent 说明)

由 FCC Core 调用 `POST /api/loops/{loop_id}/cycle` 得到实际 `CycleResult`；此文只是字段解释，不是一个伪造的运行结果。

| 字段 | 说明 | 重要警告 |
|---|---|---|
| `target` | 当前目标 | 目标不等于观测 |
| `observations` | 本轮数据快照 | 检查来源、时间戳、过期或异常 |
| `errors[]` | 误差 / 容差判断 | 只有可信观测才适合作依据 |
| `proposed_actions[]` | 生成的操作建议 | 不等于已执行 |
| `executed_actions[]` | 有执行记录的操作 | 应核实执行器实质和回读；manual adapter 仅记录模拟动作 |
| `verification_status` | 本轮验证状态 | 改进结论必须来自独立、后验观测 |
| `verification_summary` | 历史动作验证结果汇总 | 不等于本轮已改善 |
| `guard_events` | 风险拦截和权限信息 | 不能跳过 |
| `next_evaluation_at` | 下次评价（如果有） | 调度不自动采集新数据 |

面向用户呈现时建议用三栏：**I 输入观测与目标 → C 判断策略 → O 偏差、建议与验证**。不展示的底层参数要保留在专家模式而非丢弃。
