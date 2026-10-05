"""按用户限制搜图频率"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from astrbot.api.event import AstrMessageEvent


class SearchCooldown:
    """按“平台 + 发送者”跨会话限制搜图频率，AstrBot 管理员不受限制"""

    def __init__(
        self,
        cooldown_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cooldown_seconds = cooldown_seconds
        self._last_search: dict[tuple[str, str], float] = {}
        self._clock = clock

    def _get_key(self, event: AstrMessageEvent) -> tuple[str, str] | None:
        """获取限流键；未启用、管理员或无法识别发送者时返回 None"""
        if self.cooldown_seconds <= 0 or event.is_admin():
            return None
        sender_id = str(event.get_sender_id() or "")
        if not sender_id:
            return None
        return (str(event.get_platform_name() or ""), sender_id)

    def get_remaining(self, event: AstrMessageEvent) -> int:
        """返回剩余冷却秒数（向上取整），无需等待时返回 0"""
        key = self._get_key(event)
        last_search = self._last_search.get(key) if key else None
        if last_search is None:
            return 0
        remaining = last_search + self.cooldown_seconds - self._clock()
        return math.ceil(remaining) if remaining > 0 else 0

    def try_acquire(self, event: AstrMessageEvent) -> int:
        """检查并记录一次搜索

        Args:
            event: 发起搜索的消息事件

        Returns:
            冷却中时返回剩余秒数且不记录；否则记录本次搜索并返回 0
        """
        remaining = self.get_remaining(event)
        if remaining:
            return remaining
        key = self._get_key(event)
        if key is not None:
            now = self._clock()
            # 顺带清理已过期的记录，避免长期运行时持续增长
            self._last_search = {
                existing_key: last_search
                for existing_key, last_search in self._last_search.items()
                if last_search + self.cooldown_seconds > now
            }
            self._last_search[key] = now
        return 0
