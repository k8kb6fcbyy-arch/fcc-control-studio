"""HTTP 接入示例：直接打已经跑起来的服务。

先起服务：
    python -m fcc.server --port 8099

再跑：
    python examples/http_client.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8099"


def call(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read().decode()[:400]}")
        raise


CONFIG = {
    "name": "HTTP 示例回路",
    "enabled": False,
    "goals": [{"goal_id": "g1", "target_variable": "temp", "direction": "approach",
               "setpoint": {"kind": "value", "value": 50.0}, "tolerance": 1.0}],
    "observations": [{"metric_id": "temp", "source": "manual", "unit": "C",
                      "freshness_limit": 120, "missing_policy": "hold_last",
                      "outlier_policy": "flag"}],
    "controller": {"kind": "threshold",
                   "params": {"proportional_gain": 0.5, "deadband": 0.3,
                              "hysteresis": 0.3, "law": "tracking"}},
    "scheduler": {"mode": "manual"},
    "actuator": {"permission_mode": "require_approval", "adapter": "manual"},
    "verification": {"delay_cycles": 1},
}


def main() -> None:
    health = call("GET", "/api/health")
    print("健康检查:", health)

    caps = call("GET", "/api/capabilities")
    print("能力统计:", caps["counts"])
    for name in ("controller.pid", "learning.auto_upgrade", "verification"):
        print(f"  {name:28s} -> {caps['modules'][name]['status']}")

    loop = call("POST", "/api/loops", {"config": CONFIG})["loop_id"]
    print("新建 loop:", loop)

    for value in (10.0, 30.0, 46.0):
        call("POST", f"/api/loops/{loop}/observations",
             {"metric_id": "temp", "value": value, "source_type": "measured"})
        cycle = call("POST", f"/api/loops/{loop}/cycle")
        print(f"  观测 {value:5.1f} -> 偏差 {cycle['errors'][0]['error']:+.2f} "
              f"状态 {cycle['verification_status']}")

    pending = call("GET", "/api/actions/pending")["actions"]
    print(f"\n待审批 {len(pending)} 条")
    for action in pending:
        out = call("POST", f"/api/actions/{action['id']}/approve")
        print(f"  批准 #{action['id']} -> {out['action']['status']}")

    state = call("GET", f"/api/loops/{loop}")
    print("\n最近一轮:")
    print(json.dumps({k: state["last_cycle"][k] for k in
                      ("cycle_id", "verification_status", "should_intervene",
                       "human_review_required")}, ensure_ascii=False, indent=2))
    print("\n验证报告:", call("GET", f"/api/loops/{loop}/verification")["counts"])
    print("策略统计:", state["strategy_stats"]["strategy_versions"])


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print("失败:", type(exc).__name__, exc)
        print("请先运行：python -m fcc.server --port 8099")
        sys.exit(1)
