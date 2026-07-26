from __future__ import annotations

import atexit
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

TEST_ASTRBOT_ROOT = tempfile.TemporaryDirectory(
    prefix="astrbot-imgexploration-tests-"
)
atexit.register(TEST_ASTRBOT_ROOT.cleanup)
os.environ["ASTRBOT_ROOT"] = TEST_ASTRBOT_ROOT.name

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASTRBOT_ROOT = PLUGIN_ROOT.parents[2]
PLUGIN_PARENT = PLUGIN_ROOT.parent
for import_root in (ASTRBOT_ROOT, PLUGIN_PARENT):
    import_root_str = str(import_root)
    if import_root_str not in sys.path:
        sys.path.insert(0, import_root_str)

from astrbot.core.message.components import Reply  # noqa: E402
from astrbot_plugin_imgexploration.main import (  # noqa: E402
    ImgExplorationPlugin,
)
from astrbot_plugin_imgexploration.models import (  # noqa: E402
    ExplorationResult,
    SearchResultItem,
)


class FakeEvent:
    def __init__(
        self,
        timeline: list[tuple[str, object]],
        *,
        message_str: str = "搜图",
        messages: list[object] | None = None,
    ) -> None:
        self.timeline = timeline
        self.message_str = message_str
        self._messages = messages or []

    def get_messages(self) -> list[object]:
        return self._messages

    @staticmethod
    def plain_result(text: str) -> str:
        return text

    async def send(self, message: object) -> None:
        self.timeline.append(("send", message))


class RecordingService:
    def __init__(
        self,
        timeline: list[tuple[str, object]],
        result: ExplorationResult,
    ) -> None:
        self.timeline = timeline
        self.result = result

    async def explore(
        self,
        image_url: str,
        strategy_names: list[str] | None = None,
    ) -> ExplorationResult:
        self.timeline.append(("explore", (image_url, strategy_names)))
        return self.result


class CommandSearchFlowTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_plugin(service: object) -> ImgExplorationPlugin:
        plugin = object.__new__(ImgExplorationPlugin)
        plugin.service = service
        return plugin

    async def test_acknowledges_before_conversion_and_search(self) -> None:
        timeline: list[tuple[str, object]] = []
        item = SearchResultItem(title="Result", url="https://result.example")
        service = RecordingService(timeline, ExplorationResult(items=[item]))
        plugin = self.make_plugin(service)

        async def convert_image(source: str) -> str:
            timeline.append(("convert", source))
            return "https://image.example/source.jpg"

        async def send_results(_event: object, items: list[SearchResultItem]) -> None:
            timeline.append(("results", items))

        plugin._send_search_results = send_results
        event = FakeEvent(timeline)

        with patch(
            "astrbot_plugin_imgexploration.main.get_http_image_url",
            new=convert_image,
        ):
            terminal_message = await plugin._run_command_search(
                event,
                "https://image.example/original.jpg",
                ["saucenao"],
            )

        self.assertIsNone(terminal_message)
        self.assertEqual(
            timeline,
            [
                ("send", "搜索中..."),
                ("convert", "https://image.example/original.jpg"),
                (
                    "explore",
                    ("https://image.example/source.jpg", ["saucenao"]),
                ),
                ("results", [item]),
            ],
        )

    async def test_reports_image_conversion_failure_without_searching(self) -> None:
        timeline: list[tuple[str, object]] = []
        service = RecordingService(timeline, ExplorationResult())
        plugin = self.make_plugin(service)
        plugin._send_search_results = AsyncMock()
        event = FakeEvent(timeline)

        with patch(
            "astrbot_plugin_imgexploration.main.get_http_image_url",
            new=AsyncMock(return_value=None),
        ):
            terminal_message = await plugin._run_command_search(
                event,
                "invalid-source",
                None,
            )

        self.assertEqual(
            timeline,
            [
                ("send", "搜索中..."),
            ],
        )
        self.assertEqual(
            terminal_message,
            "获取图片失败",
        )
        plugin._send_search_results.assert_not_awaited()

    async def test_reports_empty_results_after_one_acknowledgement(self) -> None:
        timeline: list[tuple[str, object]] = []
        service = RecordingService(timeline, ExplorationResult())
        plugin = self.make_plugin(service)
        plugin._send_search_results = AsyncMock()
        event = FakeEvent(timeline)

        with patch(
            "astrbot_plugin_imgexploration.main.get_http_image_url",
            new=AsyncMock(return_value="https://image.example/source.jpg"),
        ):
            terminal_message = await plugin._run_command_search(
                event,
                "source",
                None,
            )

        self.assertEqual(
            timeline,
            [
                ("send", "搜索中..."),
                ("explore", ("https://image.example/source.jpg", None)),
            ],
        )
        self.assertEqual(
            terminal_message,
            "未找到相关图片来源，请尝试更换图片或稍后重试。",
        )
        plugin._send_search_results.assert_not_awaited()

    async def test_command_delegates_valid_reply_to_shared_runner(self) -> None:
        timeline: list[tuple[str, object]] = []
        service = SimpleNamespace(
            get_available_strategies=lambda: ["saucenao"],
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        plugin._get_image_from_reply = AsyncMock(
            return_value="https://image.example/reply.jpg"
        )
        plugin._run_command_search = AsyncMock(return_value=None)
        event = FakeEvent(timeline, messages=[Reply(id="123")])

        yielded = [
            result async for result in plugin.search_image_cmd(event)
        ]

        self.assertEqual(yielded, [])
        plugin._run_command_search.assert_awaited_once_with(
            event,
            "https://image.example/reply.jpg",
            None,
        )

    async def test_command_yields_terminal_message_from_runner(self) -> None:
        timeline: list[tuple[str, object]] = []
        service = SimpleNamespace(
            get_available_strategies=lambda: ["saucenao"],
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        plugin._get_image_from_reply = AsyncMock(
            return_value="https://image.example/reply.jpg"
        )
        plugin._run_command_search = AsyncMock(return_value="搜索失败")
        event = FakeEvent(timeline, messages=[Reply(id="123")])

        yielded = [
            result async for result in plugin.search_image_cmd(event)
        ]

        self.assertEqual(yielded, ["搜索失败"])


if __name__ == "__main__":
    unittest.main()
