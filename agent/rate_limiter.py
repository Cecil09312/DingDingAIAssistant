"""限流器（滑动窗口）+ 熔断器（连续失败触发）。

限流：按 user_id 维度的每分钟请求数上限，滑动窗口实现。
熔断：LLM 调用连续失败达阈值后进入熔断状态，直接拒绝请求；
     冷却时间过后进入半开状态，放行一次试探，成功则恢复，失败则继续熔断。

两者默认关闭（阈值 <= 0），需在 .env 中配置开启。
"""

import logging
import threading
import time
from collections import defaultdict, deque

logger = logging.getLogger(__name__)

# ===== 限流器（滑动窗口，按 user_id）=====
_rate_buckets: dict = defaultdict(deque)
_rate_lock = threading.Lock()


def check_rate_limit(user_id: str) -> tuple:
    """滑动窗口限流检查。

    Args:
        user_id: 用户标识

    Returns:
        (passed, reason): passed=True 表示通过；passed=False 表示被限流
    """
    from config.settings import get_settings

    settings = get_settings()
    if settings.rate_limit_per_minute <= 0:
        return True, ""

    now = time.time()
    window = 60  # 滑动窗口 60 秒
    with _rate_lock:
        bucket = _rate_buckets[user_id]
        # 清理过期记录
        while bucket and bucket[0] < now - window:
            bucket.popleft()
        if len(bucket) >= settings.rate_limit_per_minute:
            return False, f"请求过于频繁，每分钟限 {settings.rate_limit_per_minute} 次"
        bucket.append(now)
    return True, ""


# ===== 熔断器（closed / open / half_open）=====
class CircuitBreaker:
    """熔断器：连续失败达阈值后熔断，冷却后半开试探。

    状态流转：
        closed（正常）→ 连续失败达阈值 → open（熔断，拒绝所有请求）
        open → 冷却时间过后 → half_open（放行一次试探）
        half_open → 试探成功 → closed（恢复）
        half_open → 试探失败 → open（继续熔断）
    """

    def __init__(self):
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._state = "closed"
        self._lock = threading.Lock()

    def record_success(self):
        """记录一次成功调用，重置失败计数并恢复到 closed。"""
        with self._lock:
            self._failure_count = 0
            if self._state != "closed":
                logger.info("[circuit_breaker] 恢复正常 (half_open -> closed)")
            self._state = "closed"

    def record_failure(self):
        """记录一次失败调用，达阈值则进入熔断状态。"""
        from config.settings import get_settings

        settings = get_settings()
        if settings.circuit_breaker_threshold <= 0:
            return
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._state == "half_open":
                # 半开状态下失败，重新熔断
                self._state = "open"
                logger.warning("[circuit_breaker] 半开试探失败，重新熔断")
            elif self._failure_count >= settings.circuit_breaker_threshold:
                self._state = "open"
                logger.warning(
                    "[circuit_breaker] 连续失败 %d 次，进入熔断",
                    self._failure_count,
                )

    def can_pass(self) -> tuple:
        """检查是否允许请求通过。

        Returns:
            (passed, reason): passed=True 表示通过；passed=False 表示被熔断拦截
        """
        from config.settings import get_settings

        settings = get_settings()
        if settings.circuit_breaker_threshold <= 0:
            return True, ""

        with self._lock:
            if self._state == "open":
                # 检查是否已过冷却期，可进入半开状态
                if time.time() - self._last_failure_time > settings.circuit_breaker_recovery:
                    self._state = "half_open"
                    logger.info("[circuit_breaker] 冷却期过，进入半开状态试探")
                    return True, ""
                return False, "服务暂时不可用，请稍后重试"
            # closed 或 half_open 状态均放行
            return True, ""

    @property
    def state(self) -> str:
        """当前熔断器状态（closed / open / half_open）。"""
        return self._state


# 全局熔断器单例
_breaker = CircuitBreaker()


def check_circuit() -> tuple:
    """熔断检查的便捷入口。"""
    return _breaker.can_pass()


def record_success() -> None:
    """记录调用成功的便捷入口。"""
    _breaker.record_success()


def record_failure() -> None:
    """记录调用失败的便捷入口。"""
    _breaker.record_failure()


def get_circuit_state() -> str:
    """返回当前熔断器状态（供健康检查使用）。"""
    return _breaker.state
