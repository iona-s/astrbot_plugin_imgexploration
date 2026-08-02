"""图片等待状态协调"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from astrbot.api.event import AstrMessageEvent


class ImageWaitOutcome(Enum):
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True, slots=True)
class ImageWaitConsumption:
    strategy_names: tuple[str, ...] | None


ImageWaitResult = ImageWaitConsumption | ImageWaitOutcome


@dataclass(slots=True)
class ImageWaitState:
    strategy_names: list[str] | None
    expires_at: float
    future: asyncio.Future[ImageWaitResult]

    def resolve(self, result: ImageWaitResult) -> None:
        """完成等待，忽略已经结束的 Future"""
        if not self.future.done():
            self.future.set_result(result)


class ImageWaitCoordinator:
    """协调按会话和发送者隔离的图片等待"""

    def __init__(
        self,
        timeout_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self._states: dict[tuple[str, str], ImageWaitState] = {}
        self._lock = asyncio.Lock()
        self._clock = clock

    @staticmethod
    def _get_key(event: AstrMessageEvent) -> tuple[str, str]:
        """获取按会话和发送者隔离的等待键"""
        return (
            str(event.unified_msg_origin),
            str(event.get_sender_id() or ""),
        )

    def _cleanup_expired_locked(self, now: float) -> None:
        """清理过期等待；调用方必须持有等待锁"""
        expired_states = [
            (key, state)
            for key, state in self._states.items()
            if state.expires_at <= now
        ]
        for key, state in expired_states:
            self._states.pop(key, None)
            state.resolve(ImageWaitOutcome.TIMED_OUT)

    async def create(
        self,
        event: AstrMessageEvent,
        strategy_names: list[str] | None,
    ) -> ImageWaitState | None:
        """创建图片等待；已有有效等待时返回 None"""
        key = self._get_key(event)
        async with self._lock:
            now = self._clock()
            self._cleanup_expired_locked(now)
            if key in self._states:
                return None
            state = ImageWaitState(
                strategy_names=list(strategy_names) if strategy_names else None,
                expires_at=now + self.timeout_seconds,
                future=asyncio.get_running_loop().create_future(),
            )
            self._states[key] = state
        return state

    async def clear(
        self,
        event: AstrMessageEvent,
        expected_state: ImageWaitState | None = None,
    ) -> None:
        """清除当前等待；指定状态时仅清除仍匹配的等待"""
        now = self._clock()
        key = self._get_key(event)
        async with self._lock:
            state = self._states.get(key)
            if state is not None and (
                expected_state is None or state is expected_state
            ):
                self._states.pop(key, None)
                state.resolve(ImageWaitOutcome.CANCELLED)
            self._cleanup_expired_locked(now)

    async def consume(
        self,
        event: AstrMessageEvent,
    ) -> ImageWaitConsumption | None:
        """原子消费图片等待，并返回已保存的搜索策略"""
        now = self._clock()
        key = self._get_key(event)
        async with self._lock:
            state = self._states.pop(key, None)
            self._cleanup_expired_locked(now)
            if state is None:
                return None
            if state.expires_at <= now:
                state.resolve(ImageWaitOutcome.TIMED_OUT)
                return None
            consumption = ImageWaitConsumption(
                strategy_names=(
                    tuple(state.strategy_names) if state.strategy_names else None
                ),
            )
            state.resolve(consumption)
            return consumption

    async def wait(
        self,
        event: AstrMessageEvent,
        state: ImageWaitState,
    ) -> ImageWaitResult:
        """等待图片提交，并在截止时间到达时自动返回超时结果"""
        key = self._get_key(event)
        remaining = max(0.0, state.expires_at - self._clock())
        try:
            return await asyncio.wait_for(
                asyncio.shield(state.future),
                timeout=remaining,
            )
        except TimeoutError:
            async with self._lock:
                if self._states.get(key) is state:
                    self._states.pop(key, None)
                    state.resolve(ImageWaitOutcome.TIMED_OUT)
            return await asyncio.shield(state.future)
        except asyncio.CancelledError:
            await self.clear(event, expected_state=state)
            raise

    async def close(self) -> None:
        """取消并清空全部图片等待"""
        async with self._lock:
            states = list(self._states.values())
            self._states.clear()
            for state in states:
                state.resolve(ImageWaitOutcome.CANCELLED)
