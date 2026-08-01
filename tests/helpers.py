from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from astrbot_plugin_imgexploration.core.image_wait import ImageWaitCoordinator
from astrbot_plugin_imgexploration.main import ImgExplorationPlugin


class FakeEvent:
    def __init__(
        self,
        timeline: list[tuple[str, object]],
        *,
        message_str: str = "搜图",
        messages: list[object] | None = None,
        unified_msg_origin: str = "test:group:1",
        sender_id: str = "user-1",
        is_command: bool | None = None,
        raw_message: object | None = None,
        message_id: str = "message-1",
    ) -> None:
        self.timeline = timeline
        self.message_str = message_str
        self._messages = messages or []
        self.unified_msg_origin = unified_msg_origin
        self._sender_id = sender_id
        self.message_obj = SimpleNamespace(
            message_id=message_id,
            raw_message=raw_message,
        )
        if is_command is None:
            parts = message_str.strip().split(maxsplit=1)
            is_command = bool(parts) and parts[0] == "搜图"
        self.is_at_or_wake_command = is_command

    def get_messages(self) -> list[object]:
        return self._messages

    def get_sender_id(self) -> str:
        return self._sender_id

    @staticmethod
    def plain_result(text: str) -> str:
        return text

    async def send(self, message: object) -> None:
        self.timeline.append(("send", message))


class PluginTestCase(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_plugin(service: object) -> ImgExplorationPlugin:
        plugin = object.__new__(ImgExplorationPlugin)
        plugin.service = service
        plugin._image_wait = ImageWaitCoordinator(
            60,
            clock=Mock(return_value=0.0),
        )
        return plugin
