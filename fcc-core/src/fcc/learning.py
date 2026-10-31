"""Learning Manager：P1 **只做统计**，不做自动策略升级。

能回答的是「某策略 N 次干预里 improved 几次」这种可核查的问题；
不能回答的是「下一版策略该是什么」——那属于 learning.auto_upgrade，明确 not_implemented。
"""

from __future__ import annotations

from typing import Any

from .capability import CAPABILITIES
from .store import Store


class LearningManager:
    def __init__(self, store: Store, loop_id: str) -> None:
        self.store = store
        self.loop_id = loop_id

    # -- 统计 -------------------------------------------------------------

    def strategy_stats(self) -> dict[str, Any]:
        actions = self.store.list_actions(self.loop_id, limit=2000)
        verifications = {v["action_id"]: v for v in
                         self.store.list_verifications(self.loop_id, limit=2000)}

        buckets: dict[str, dict[str, Any]] = {}
        for action in actions:
            version = str(action.get("strategy_version") or 1)
            bucket = buckets.setdefault(
                version,
                {
                    "strategy_version": int(version),
                    "interventions": 0,
                    "improved": 0,
                    "unchanged": 0,
                    "worsened": 0,
                    "failed": 0,
                    "inconclusive": 0,
                    "pending": 0,
                    "by_source_type": {},
                },
            )
            bucket["interventions"] += 1
            source = action.get("source_type") or "unknown"
            bucket["by_source_type"][source] = bucket["by_source_type"].get(source, 0) + 1

            verdict = action.get("verification_status")
            if verdict is None:
                bucket["pending"] += 1
                continue
            if verdict in bucket:
                bucket[verdict] += 1
            else:
                bucket["pending"] += 1
                continue
            _ = verifications  # 保留结构，后续按 action_id 关联明细

        for bucket in buckets.values():
            judged = bucket["interventions"] - bucket["pending"]
            bucket["improved_rate"] = (
                round(bucket["improved"] / judged, 4) if judged else 0.0
            )
            bucket["judged"] = judged

        return {
            "loop_id": self.loop_id,
            "strategy_versions": sorted(buckets.values(),
                                        key=lambda b: b["strategy_version"]),
            "action_counts": self._status_counts(actions),
        }

    @staticmethod
    def _status_counts(actions: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for a in actions:
            counts[a["status"]] = counts.get(a["status"], 0) + 1
        return counts

    def goal_stats(self) -> dict[str, Any]:
        actions = self.store.list_actions(self.loop_id, limit=2000)
        out: dict[str, dict[str, int]] = {}
        for a in actions:
            goal = a.get("goal_id") or "?"
            bucket = out.setdefault(goal, {"interventions": 0, "improved": 0,
                                           "worsened": 0, "unchanged": 0,
                                           "failed": 0, "inconclusive": 0})
            bucket["interventions"] += 1
            verdict = a.get("verification_status")
            if verdict in bucket:
                bucket[verdict] += 1
        return out

    # -- 明确不做的 -------------------------------------------------------

    def propose_upgrade(self) -> dict[str, Any]:
        return {
            "implemented": False,
            "status": "not_implemented",
            "reason": CAPABILITIES["learning.auto_upgrade"]["note"],
        }

    def system_identification(self) -> dict[str, Any]:
        return {
            "implemented": False,
            "status": "not_implemented",
            "reason": CAPABILITIES["learning.system_identification"]["note"],
        }

    def daily_digest(self) -> dict[str, Any]:
        return {
            "implemented": False,
            "status": "not_implemented",
            "reason": CAPABILITIES["learning.daily_digest"]["note"],
        }
