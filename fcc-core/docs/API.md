> **Version note (FCC Core 1.2):** This file includes historic FCC 1.0/1.1 design or API context. For implemented advanced methods, see `ADVANCED-CONTROL.md` and the generated `CAPABILITY.md`. Any text below saying MPC/Kalman/PID are interface-only describes an older version, not this release.

> **FCC 1.1 提醒：** `controller.pid` 现已实现。`outcome_unknown` 表示执行结果不明而不是 `failed`，且 action 的执行仅允许 `approved`。以 `src/fcc/models.py` 和 OpenAPI 作为最新输入定义。

# FCC API 参考（P1）

两条通道语义一致：Python SDK（`from fcc import Client`）与 HTTP（`/api/...`）。
所有时间戳在存储与传输层统一为 **UTC ISO8601**（带 `+00:00`），只在面板上转本地。

---

## 1. HTTP 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查（A1） |
| GET | `/api/capabilities` | 能力台账：每个模块 `implemented / interface_only / not_implemented`（A12） |
| GET | `/api/adapters` | 已注册的执行适配器 / 观测源 / 模拟对象 |
| POST | `/api/sim/plants` | 建一个模拟被控对象，返回适配器名 |
| POST | `/api/sim/plants/{name}/step` | 手动推进模拟对象 |
| GET | `/api/loops` | 回路列表 |
| POST | `/api/loops` | 建回路，body `{"config": {...}}` |
| GET | `/api/loops/{id}` | 状态：配置 + 观测 + 历史 + 待审批 + 策略统计 |
| DELETE | `/api/loops/{id}` | 删除回路及其全部数据 |
| POST | `/api/loops/{id}/start` `/pause` `/resume` `/stop` | 启停（`enabled` 落库，重启不丢） |
| POST | `/api/loops/{id}/cycle` | 手动跑一轮，可带 `?event_key=` 做事件幂等 |
| GET | `/api/loops/{id}/history?limit=` | 周期历史 |
| GET | `/api/loops/{id}/verification` | 五态验证统计 + 明细 |
| PATCH | `/api/loops/{id}/config` | 局部改配置（深合并，数组整体替换） |
| POST | `/api/loops/{id}/rollback-strategy/{version}` | 回滚到某个策略版本 |
| POST | `/api/loops/{id}/observations` | 提交观测 |
| POST | `/api/loops/{id}/feedback` | 人工反馈：批准 / 驳回 / 直接给设定值 |
| GET | `/api/loops/{id}/audit?cycle_id=` | 审计链（A7） |
| GET | `/api/actions/pending` | 全部待审批动作 |
| GET | `/api/actions?loop_id=` | 动作列表 |
| POST | `/api/actions/{id}/approve` `/reject` `/rollback` | 审批与回滚 |
| GET | `/` | 单页面板 |

错误码：`400` 配置非法 / `404` 资源不存在 / `422` 观测字段不合法（含 AI 合同校验）/ `500` 周期内部异常。

---

## 2. 配置 Schema

```jsonc
{
  "name": "string",
  "enabled": false,                          // 建时决定是否立即调度

  "goals": [{                                // 至少 1 个
    "goal_id": "g1",
    "target_variable": "temp",               // 必须是已注册的观测变量
    "direction": "approach",                 // increase | decrease | approach | maintain
    "setpoint": {"kind": "value", "value": 50},
    // 或 {"kind": "range", "low": 48, "high": 52}
    "tolerance": 1.0,
    "priority": 5,                           // 1..9，数字小=优先
    "constraints": {}
  }],

  "observations": [{                         // 至少 1 个
    "metric_id": "temp",
    "source": "manual",                      // manual 或已注册的观测适配器名
    "value_type": "float",
    "unit": "C",
    "sampling_interval": 5.0,
    "valid_range": [-50, 150],               // 硬范围
    "freshness_limit": 120.0,                // 秒；超过即 stale
    "missing_policy": "hold_last",           // hold_last | skip | stale
    "outlier_policy": "flag",                // flag | drop | clamp
    "outlier_method": "sigma3",              // sigma3 | iqr
    "outlier_window": 12,
    "importance": "normal"
  }],

  "controller": {
    "kind": "threshold",                     // 见 §3
    "params": {},
    "strategy_version": 1
  },

  "scheduler": {
    "mode": "manual",                        // manual | fixed | event | threshold
    "interval_seconds": 30,
    "interval_unit": "second"                // second | minute | hour
  },

  "actuator": {
    "permission_mode": "require_approval",   // auto | require_approval | suggest_only
    "allow_ai_auto": false,                  // 极窄例外，见 §4
    "adapter": "manual",
    "timeout_seconds": 10,
    "max_retries": 1,
    "retry_backoff_seconds": 0,
    "action_range": [0, 100],
    "max_step": 20                           // 单次最大调整幅度（防过度修正）
  },

  "verification": {
    "delay_cycles": 1,                       // 至少 1：同轮内不可能有新观测
    "delay_seconds": 0,
    "noise_k": 2.0
  },

  "metadata": {}
}
```

### 误差符号约定

`error` 一律定义为 **正数表示"需要把被控量往上推"**：

- 单点目标：`error = setpoint - value`
- 区间目标：区间内 `error = 0`；低于下界 `error = low - value`（正）；高于上界 `error = high - value`（负）

`direction` 只影响语义与冲突检测，不影响误差符号。

---

## 3. 控制器

| kind | 状态 | 关键 params |
|---|---|---|
| `monitor` | implemented | 无 |
| `threshold` | implemented | `proportional_gain`、`deadband`、`hysteresis`、`cooldown_cycles`、`min_delta`、`law` |
| `rule` | implemented | `rules: [{id, if:{metric\|goal, op, value}, action:{value, risk, goal_id}, reason}]` |
| `ai_assisted` | implemented | `advisor`（注册名）、`static_suggestions`、`min_confidence` |
| `human_in_loop` | implemented | `proportional_gain`（只用于给建议值） |
| `pid` | **interface_only** | `kp/ki/kd/integral_limit`，`compute()` 抛 `NotImplementedError` |
| `mpc` | **interface_only** | `horizon`，`propose()` 抛 `NotImplementedError` |

理论来源不在本表里混写，统一见 `docs/CAPABILITY.md` 的 `theory_source` 列：
PID 是经典控制理论 7.10，MPC 是经典控制理论之外（优化控制分支）。

`threshold.law` 有两种动作律，别混用：

| law | 公式 | 特点 |
|---|---|---|
| `tracking`（默认） | `u = sp + K·e` | P + 设定值前馈。模拟对象上实测收敛快、无过冲（工程观察，非稳定性证明）；**对恒定扰动有静差** `y = sp + d/(1+K)` |
| `incremental` | `u = u + K·e` | 等效积分。**无静差**；增益偏大时易振荡 |

> 关于 PID：P1 不实现 PID 的 `ki`/`kd`（积分抗饱和、微分对噪声的放大都没处理）。
> `incremental` 律不是 PID —— 它是固定增益增量律，没有微分项、没有抗饱和。

---

## 4. 三条硬规则（代码级，不可配置关闭）

### 规则 A：AI 不能是裁判

- `ai_inference` 观测必须带 `confidence`（0~1）、`evidence`（≥1 条），`is_ground_truth` 恒为 `false`，否则 **422 拒收**。
- 某个偏差**只有 ai_inference 支持**时：`should_intervene = false`、`human_review_required = true`、
  该目标记为 `ai_only`。Engine 里还有第二道闸，任何控制器为该目标产出的动作都会被 `[FCC_GUARD] ai_only_deviation_blocked` 拦掉。

### 规则 B：AI 动作一律审批

- `source_type = ai_inference` 的动作提案，即使 `permission_mode = auto` 也强制降级为 `require_approval`，
  决策原因里写 `[FCC_GUARD] ai_action_downgraded`，审计里写一条 `guard`。
- 唯一例外需**同时**满足：`allow_ai_auto = true` **且** `risk_level = low` **且** 登记了 `rollback_method`。

### 规则 C：没有验证结果不许声称成功

- 动作执行成功后状态只能是 `pending_verification`，`verification_status` 也只能是 `pending_verification`。
- 只有 `Verifier` 基于**重新观测**给出 `improved / unchanged / worsened / failed` 才落库；
  `inconclusive` 不算成功，动作继续挂 `pending_verification`（最多重试 3 轮，之后等新观测）。

---

## 5. 观测提交

```jsonc
// measured / human_input
{"metric_id": "temp", "value": 42.0, "source_type": "measured"}

// ai_inference：confidence + evidence 必填
{
  "metric_id": "temp",
  "value": 88.0,
  "source_type": "ai_inference",
  "confidence": 0.93,
  "evidence": ["anomaly-model: 温度突升", "log: 3 次超阈"],
  "is_ground_truth": false,
  "model": "glm-4-flash",
  "prompt_version": "v1"
}
```

可选 `ts`（ISO8601）。不传则用服务器当前 UTC 时间。

---

## 6. 人工反馈

```jsonc
// 批准/驳回一条待审批动作
{"action_id": 12, "decision": "approve"}
{"action_id": 12, "decision": "reject", "note": "现在不能调"}

// 直接给一个设定值（source_type 记为 human_input）
{"value": 48.0, "goal_id": "g1", "note": "人工给定", "idempotency_key": "fb-001"}
```

---

## 7. 周期输出结构（`ctrl_cycle` / `CycleResult`）

```jsonc
{
  "cycle_id": 7,
  "loop_id": "loop_xxx",
  "started_at": "2026-10-30T11:00:00+00:00",
  "finished_at": "2026-10-30T11:00:00.004+00:00",
  "target": {"g1": {"setpoint": {...}, "nominal": 50.0, "band": [49, 51], "priority": 5}},
  "observations": {"temp": {"value": 42.0, "source_type": "measured", "stale": false, "flags": []}},
  "estimated_state": {"current_control": 12.0, "method": "direct_measurement", "kalman": "interface_only"},
  "errors": [{"goal_id": "g1", "error": 8.0, "out_of_tolerance": true, "ai_only": false,
              "uncertainty": 0.0, "oscillation_detected": false, "cumulative_error": 6.1}],
  "uncertainty": 0.0,
  "anomalies": ["stale:temp"],
  "proposed_actions": [...],
  "executed_actions": [{"action_id": 5, "status": "pending_verification", "source_type": "measured"}],
  "expected_outcome": {"description": "期望 |error| 在下一轮下降", "predicted_value": null},
  "verification_status": "pending_verification",
  "next_evaluation_at": null,
  "human_review_required": false,
  "should_intervene": true,
  "verifications": [{"action_id": 4, "verdict": "improved"}],
  "guard_events": [],
  "audit_ref": [12, 13, 14]
}
```

`verification_status` 取值：`no_action` / `awaiting_human` / `pending_verification` /
`failed` / `verified:<verdict 列表>` / `error`。

---

## 8. Python SDK

```python
from fcc import Client

c = Client("fcc.db")                       # 或 ":memory:"

c.create_loop(config) -> loop_id
c.start_loop / pause_loop / resume_loop / stop_loop(loop_id)
c.run_cycle(loop_id, event_key=None) -> CycleResult
c.submit_observation(loop_id, data) -> dict
c.submit_human_feedback(loop_id, feedback) -> dict
c.approve_action(action_id, by="human") -> dict
c.reject_action(action_id, by="human", reason="") -> dict
c.register_observation_adapter(name, fn)   # fn(loop_id, spec) -> 观测 dict
c.register_actuator(name, adapter)         # adapter.apply/state/rollback
c.register_controller(kind, cls)           # cls(params).propose(ctx) -> ControllerOutput
c.register_ai_advisor(name, fn)            # fn(ctx) -> [建议 dict]
c.get_state(loop_id) -> dict
c.get_history(loop_id, limit=50) -> list
c.get_verification_report(loop_id) -> dict
c.get_capabilities() -> dict
c.update_config(loop_id, patch) -> LoopConfig
c.rollback_strategy(loop_id, version) -> dict
c.rollback_action(action_id) -> dict
c.close()
```

### 适配器协议

```python
class ActuatorAdapter:
    def apply(self, action: dict) -> dict:      # 必须返回 ok / applied_value / previous_value
    def state(self) -> dict:                    # 必须能给出 current_value
    def rollback(self, action: dict, result: dict) -> dict
```

`apply` 抛异常、返回 `ok=False`、或超时，都算执行失败；按 `max_retries` 重试，
最终失败会落 `status=failed` 并写一条 `failed` 验证记录。
