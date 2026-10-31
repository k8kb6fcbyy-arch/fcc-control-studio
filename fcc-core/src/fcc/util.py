"""通用小工具：UTC 时间、数值裁剪、JSON 安全序列化。

约定（派单包 §10.6）：数据库内一切时间戳均为 UTC ISO8601（带 +00:00），
只在展示层转本地时区。本模块不依赖任何第三方库。
"""

from __future__ import annotations

import datetime as dt
import json
import math
from typing import Any

UTC = dt.timezone.utc


def utcnow() -> dt.datetime:
    return dt.datetime.now(UTC)


def utcnow_iso() -> str:
    return utcnow().isoformat()


def to_iso(ts: dt.datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat()


def parse_iso(value: str) -> dt.datetime:
    """解析 ISO8601；无时区视为 UTC（避免本地时区污染存储）。"""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def seconds_between(start_iso: str, end_iso: str) -> float:
    return (parse_iso(end_iso) - parse_iso(start_iso)).total_seconds()


def clamp(value: float, low: float | None, high: float | None) -> float:
    if low is not None and value < low:
        value = low
    if high is not None and value > high:
        value = high
    return value


def isfinite(*values: float | None) -> bool:
    return all(v is not None and math.isfinite(v) for v in values)


def round_sig(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    if not math.isfinite(value):
        return None
    return round(value, digits)


def dumps(obj: Any) -> str:
    """NaN/Inf 不是合法 JSON，这里递归降级为 null，避免写入脏数据。"""
    return json.dumps(_sanitize(obj), ensure_ascii=False, default=_fallback, allow_nan=False)


def _sanitize(obj: Any) -> Any:
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if hasattr(obj, "model_dump"):
        return _sanitize(obj.model_dump())
    return obj


def loads(text: str | None, default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return default


def _fallback(obj: Any) -> Any:
    if isinstance(obj, dt.datetime):
        return to_iso(obj)
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return str(obj)


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mu = sum(values) / len(values)
    var = sum((v - mu) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("quantile() 需要非空列表")
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_values[int(pos)]
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac
