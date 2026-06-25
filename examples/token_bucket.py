"""
线程安全的令牌桶限流器 (Token Bucket Rate Limiter)。
支持确定性时间模拟，避免 flaky tests。
"""
import time
import threading
from typing import Callable, Optional


class TokenBucket:
    """
    线程安全的令牌桶限流器。

    Args:
        capacity: 桶的容量（最大令牌数）。
        refill_rate_per_sec: 每秒填充的令牌数。
        time_func: 可选的时间函数，用于确定性测试。
                   默认使用 time.monotonic。
    """

    def __init__(
        self,
        capacity: float,
        refill_rate_per_sec: float,
        time_func: Optional[Callable[[], float]] = None,
    ):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if refill_rate_per_sec < 0:
            raise ValueError("refill_rate_per_sec must be non-negative")
        self._capacity = capacity
        self._refill_rate = refill_rate_per_sec
        self._time_func = time_func or time.monotonic
        self._lock = threading.Lock()
        self._tokens: float = capacity
        self._last_refill: float = self._time_func()

    def _refill(self, now: float) -> None:
        if self._refill_rate == 0:
            return
        delta = now - self._last_refill
        if delta > 0:
            added = delta * self._refill_rate
            self._tokens = min(self._capacity, self._tokens + added)
            self._last_refill = now

    def consume(self, tokens: float = 1.0) -> bool:
        """
        尝试消费指定数量的令牌。
        Returns True if consumed, False if not enough tokens.
        """
        with self._lock:
            now = self._time_func()
            self._refill(now)
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    @property
    def tokens(self) -> float:
        """线程安全地获取当前可用令牌数。"""
        with self._lock:
            self._refill(self._time_func())
            return self._tokens
