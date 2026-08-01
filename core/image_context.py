"""图片上下文管理器.

维护每个会话中出现的图片 URL，供 LLM 工具调用时使用。
支持会话级隔离和全局隔离模式。
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from astrbot.api import logger


@dataclass
class ImageInfo:
    """图片信息."""

    image_id: str
    url: str
    timestamp: datetime = field(default_factory=datetime.now)
    message_id: str | None = None
    sender_id: str | None = None
    description: str | None = None  # 可选的图片描述


@dataclass
class SessionImages:
    """单个会话的图片存储."""

    images: OrderedDict[str, ImageInfo] = field(default_factory=OrderedDict)
    url_index: dict[str, str] = field(default_factory=dict)
    max_images: int = 20  # 每个会话最多存储的图片数量

    def _remove_image(self, image_id: str) -> None:
        info = self.images.pop(image_id, None)
        if info:
            self.url_index.pop(info.url, None)

    def add_image(
        self,
        url: str,
        message_id: str | None = None,
        sender_id: str | None = None,
    ) -> ImageInfo | None:
        """添加图片到会话。"""
        if not url or not url.startswith(("http://", "https://")):
            return None

        # URL 已存在时，视为最新一次出现并刷新顺序。
        old_image_id = self.url_index.get(url)
        if old_image_id:
            self._remove_image(old_image_id)

        while len(self.images) >= self.max_images:
            oldest_image_id = next(iter(self.images.keys()))
            self._remove_image(oldest_image_id)

        info = ImageInfo(
            image_id=uuid4().hex[:12],
            url=url,
            message_id=message_id,
            sender_id=sender_id,
        )
        self.images[info.image_id] = info
        self.url_index[url] = info.image_id
        return info

    def prune_expired(self, ttl_seconds: int) -> int:
        """清理过期图片，返回清理数量。"""
        if ttl_seconds <= 0 or not self.images:
            return 0

        now = datetime.now()
        expired_ids = [
            image_id
            for image_id, info in self.images.items()
            if (now - info.timestamp).total_seconds() > ttl_seconds
        ]
        for image_id in expired_ids:
            self._remove_image(image_id)
        return len(expired_ids)

    def get_recent_image_info(self) -> ImageInfo | None:
        """获取最近的图片信息。"""
        if not self.images:
            return None
        return next(reversed(self.images.values()))

    def get_image_info_by_index(self, index: int) -> ImageInfo | None:
        """根据索引获取图片信息（1-based，按时间从旧到新）。"""
        if index < 1 or index > len(self.images):
            return None
        return list(self.images.values())[index - 1]

    def get_image_info_by_id(self, image_id: str) -> ImageInfo | None:
        """根据 image_id 获取图片信息。"""
        return self.images.get(image_id)

    def get_all_image_infos(self) -> list[ImageInfo]:
        """获取所有图片信息（从旧到新）。"""
        return list(self.images.values())

    def clear(self) -> None:
        """清空会话中的所有图片。"""
        self.images.clear()
        self.url_index.clear()


class ImageContextManager:
    """图片上下文管理器。

    支持两种隔离模式：
    - session: 会话级隔离（每个群/私聊独立）
    - global: 全局共享（所有会话共享）
    """

    def __init__(
        self,
        isolation_mode: str = "session",
        max_images_per_session: int = 20,
        ttl_seconds: int = 0,
        max_sessions: int = 200,
        include_url_in_context: bool = True,
    ):
        """初始化图片上下文管理器。

        Args:
            isolation_mode: 隔离模式，"session" 或 "global"
            max_images_per_session: 每个会话最大图片数量
            ttl_seconds: 图片过期时间（秒），0 表示不过期
            max_sessions: 最大会话数量（仅在会话级隔离模式下生效）
            include_url_in_context: 是否在上下文信息中包含图片 URL
        """
        self.isolation_mode = isolation_mode
        self.max_images_per_session = max(1, int(max_images_per_session))
        self.ttl_seconds = max(0, int(ttl_seconds))
        self.max_sessions = max(1, int(max_sessions))
        self.include_url_in_context = include_url_in_context

        # 会话级存储，OrderedDict 用于 LRU 回收。
        self._sessions: OrderedDict[str, SessionImages] = OrderedDict()
        self._global_session = SessionImages(max_images=self.max_images_per_session)

        # 使用同步锁保护共享结构，避免并发读写导致状态抖动。
        self._lock = threading.RLock()

    def _get_session_key(self, event: Any) -> str:
        """获取会话标识 key。

        Args:
            event: 消息事件

        Returns:
            会话标识
        """
        # 尝试获取会话 ID
        session_id = getattr(event, "session_id", None)
        if session_id:
            return str(session_id)

        # 回退：组合平台 + 群号/用户号
        platform = getattr(event, "platform", "unknown")
        group_id = getattr(event, "group_id", None)
        user_id = getattr(event, "user_id", None)

        if group_id:
            return f"{platform}:group:{group_id}"
        if user_id:
            return f"{platform}:user:{user_id}"
        return f"{platform}:unknown"

    def _evict_stale_sessions_if_needed(self) -> None:
        if self.isolation_mode == "global":
            return
        while len(self._sessions) > self.max_sessions:
            evicted_key, _ = self._sessions.popitem(last=False)
            logger.debug(
                f"[ImageContext] 会话缓存达到上限，已回收最旧会话: {evicted_key}"
            )

    def _get_session(self, event: Any) -> SessionImages:
        """获取会话存储。

        Args:
            event: 消息事件

        Returns:
            会话存储对象
        """
        if self.isolation_mode == "global":
            return self._global_session

        session_key = self._get_session_key(event)
        existing = self._sessions.pop(session_key, None)
        if existing is None:
            existing = SessionImages(max_images=self.max_images_per_session)
        # 访问即刷新 LRU 顺序
        self._sessions[session_key] = existing
        self._evict_stale_sessions_if_needed()
        return existing

    def _prepare_session(self, session: SessionImages) -> None:
        """读取前按策略清理过期图片。"""
        if self.ttl_seconds > 0:
            removed = session.prune_expired(self.ttl_seconds)
            if removed > 0:
                logger.debug(f"[ImageContext] 已清理 {removed} 张过期图片")

    def add_image(
        self,
        event: Any,
        url: str,
        message_id: str | None = None,
        sender_id: str | None = None,
    ) -> None:
        """添加图片到上下文。

        Args:
            event: 消息事件
            url: 图片 URL
            message_id: 消息 ID
            sender_id: 发送者 ID
        """
        with self._lock:
            session = self._get_session(event)
            info = session.add_image(url, message_id, sender_id)
            if info:
                logger.debug(
                    f"[ImageContext] 捕获图片到上下文: "
                    f"image_id={info.image_id} url={url}"
                )

    def get_recent_image(self, event: Any) -> str | None:
        """获取最近的图片 URL。

        Args:
            event: 消息事件

        Returns:
            最近的图片 URL
        """
        with self._lock:
            session = self._get_session(event)
            self._prepare_session(session)
            info = session.get_recent_image_info()
            return info.url if info else None

    def get_image_by_index(self, event: Any, index: int) -> str | None:
        """根据索引获取图片 URL。

        Args:
            event: 消息事件
            index: 图片索引（1-based，1 = 最早，-1 = 最新）

        Returns:
            图片 URL
        """
        with self._lock:
            session = self._get_session(event)
            self._prepare_session(session)
            if index == -1:
                info = session.get_recent_image_info()
            else:
                info = session.get_image_info_by_index(index)
            return info.url if info else None

    def get_image_by_id(self, event: Any, image_id: str) -> str | None:
        """根据稳定 image_id 获取图片 URL。

        Args:
            event: 消息事件
            image_id: 图片 ID

        Returns:
            图片 URL
        """
        if not image_id:
            return None
        with self._lock:
            session = self._get_session(event)
            self._prepare_session(session)
            info = session.get_image_info_by_id(image_id)
            return info.url if info else None

    def get_all_images(self, event: Any) -> list[str]:
        """获取所有图片 URL 列表（从旧到新）。

        Args:
            event: 消息事件

        Returns:
            图片 URL 列表
        """
        with self._lock:
            session = self._get_session(event)
            self._prepare_session(session)
            return [info.url for info in session.get_all_image_infos()]

    def get_image_context_info(self, event: Any) -> dict:
        """获取图片上下文信息，供 AI 参考。

        Args:
            event: 消息事件

        Returns:
            包含图片上下文信息的字典
        """
        with self._lock:
            session = self._get_session(event)
            self._prepare_session(session)
            infos = session.get_all_image_infos()

        if not infos:
            return {
                "has_images": False,
                "count": 0,
                "images": [],
                "hint": "当前会话中没有图片。",
            }

        now = datetime.now()
        images_payload: list[dict[str, Any]] = []
        for i, info in enumerate(infos):
            item: dict[str, Any] = {
                "index": i + 1,
                "image_id": info.image_id,
                "is_latest": i == len(infos) - 1,
                "timestamp": info.timestamp.isoformat(timespec="seconds"),
                "age_seconds": int((now - info.timestamp).total_seconds()),
                "message_id": info.message_id,
                "sender_id": info.sender_id,
            }
            if self.include_url_in_context:
                item["url"] = info.url
            images_payload.append(item)

        latest_index = len(infos)
        ttl_hint = (
            f" 图片超过 {self.ttl_seconds} 秒后会自动过期。"
            if self.ttl_seconds > 0
            else ""
        )
        return {
            "has_images": True,
            "count": len(infos),
            "images": images_payload,
            "hint": (
                f"当前会话中有 {len(infos)} 张图片。"
                f"第 {latest_index} 张是最新图片。"
                "建议优先使用 image_id 指定图片；也可使用 image_index（1=最早，-1=最新）。"
                f"{ttl_hint}"
            ),
        }

    def clear_session(self, event: Any) -> None:
        """清空指定会话的图片。

        Args:
            event: 消息事件
        """
        with self._lock:
            if self.isolation_mode == "global":
                self._global_session.clear()
                return

            session_key = self._get_session_key(event)
            self._sessions.pop(session_key, None)

    def clear_all(self) -> None:
        """清空所有会话的图片。"""
        with self._lock:
            self._global_session.clear()
            self._sessions.clear()


# 全局图片上下文管理器实例
_image_context_manager: ImageContextManager | None = None


def init_image_context_manager(
    isolation_mode: str = "session",
    max_images: int = 20,
    ttl_seconds: int = 0,
    max_sessions: int = 200,
    include_url_in_context: bool = True,
) -> ImageContextManager:
    """初始化全局图片上下文管理器。

    Args:
        isolation_mode: 隔离模式
        max_images: 每个会话最大图片数
        ttl_seconds: 图片过期时间（秒），0 表示不过期
        max_sessions: 最大会话数量（仅在会话级隔离模式下生效）
        include_url_in_context: 是否在上下文信息中包含图片 URL

    Returns:
        图片上下文管理器实例
    """
    global _image_context_manager
    _image_context_manager = ImageContextManager(
        isolation_mode=isolation_mode,
        max_images_per_session=max_images,
        ttl_seconds=ttl_seconds,
        max_sessions=max_sessions,
        include_url_in_context=include_url_in_context,
    )
    logger.info(
        "[ImageContext] 已初始化，隔离模式: %s, max_images: %s, ttl_seconds: %s, max_sessions: %s",
        isolation_mode,
        max_images,
        ttl_seconds,
        max_sessions,
    )
    return _image_context_manager


def get_image_context_manager() -> ImageContextManager:
    """获取全局图片上下文管理器。

    Returns:
        图片上下文管理器实例
    """
    global _image_context_manager
    if _image_context_manager is None:
        _image_context_manager = ImageContextManager()
    return _image_context_manager
