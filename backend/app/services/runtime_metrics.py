from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class _LatencyBucket:
    latency_ms: deque[float] = field(default_factory=lambda: deque(maxlen=500))
    first_token_ms: deque[float] = field(default_factory=lambda: deque(maxlen=500))


class RuntimeMetrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self._chat_by_user: defaultdict[int, _LatencyBucket] = defaultdict(_LatencyBucket)

    def record_chat_request(
        self,
        owner_user_id: int,
        *,
        latency_ms: float,
        first_token_ms: float | None = None,
    ) -> None:
        with self._lock:
            bucket = self._chat_by_user[owner_user_id]
            bucket.latency_ms.append(latency_ms)
            if first_token_ms is not None:
                bucket.first_token_ms.append(first_token_ms)

    def chat_summary(self, owner_user_id: int) -> dict[str, float | int | None]:
        with self._lock:
            bucket = self._chat_by_user.get(owner_user_id)
            if bucket is None:
                latencies: list[float] = []
                first_tokens: list[float] = []
            else:
                latencies = list(bucket.latency_ms)
                first_tokens = list(bucket.first_token_ms)
        return {
            "chat_request_count": len(latencies),
            "chat_latency_p95_ms": _percentile(latencies, 95),
            "chat_latency_p99_ms": _percentile(latencies, 99),
            "chat_first_token_count": len(first_tokens),
            "chat_first_token_p95_ms": _percentile(first_tokens, 95),
            "chat_first_token_p99_ms": _percentile(first_tokens, 99),
        }


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((percentile / 100) * (len(ordered) - 1))
    return ordered[index]


runtime_metrics = RuntimeMetrics()
