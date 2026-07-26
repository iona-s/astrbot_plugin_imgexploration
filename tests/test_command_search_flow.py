from __future__ import annotations

import atexit
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

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

from astrbot.core.message.components import Image, Reply  # noqa: E402
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
                "base64://original-image",
                ["saucenao"],
            )

        self.assertIsNone(terminal_message)
        self.assertEqual(
            timeline,
            [
                ("send", "搜索中..."),
                ("convert", "base64://original-image"),
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
        image = Image(file="invalid-file", url="invalid-url")
        convert_image = AsyncMock(return_value=None)

        with patch(
            "astrbot_plugin_imgexploration.main.get_http_image_url",
            new=convert_image,
        ):
            terminal_message = await plugin._run_command_search(
                event,
                image,
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
        self.assertEqual(
            convert_image.await_args_list,
            [call("invalid-url"), call("invalid-file")],
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
        reply_image = Image(file="https://image.example/reply.jpg")
        plugin._get_image_from_reply = AsyncMock(return_value=reply_image)
        plugin._run_command_search = AsyncMock(return_value=None)
        event = FakeEvent(timeline, messages=[Reply(id="123")])

        yielded = [
            result async for result in plugin.search_image_cmd(event)
        ]

        self.assertEqual(yielded, [])
        plugin._run_command_search.assert_awaited_once_with(
            event,
            reply_image,
            None,
        )

    async def test_command_yields_terminal_message_from_runner(self) -> None:
        timeline: list[tuple[str, object]] = []
        service = SimpleNamespace(
            get_available_strategies=lambda: ["saucenao"],
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        reply_image = Image(file="https://image.example/reply.jpg")
        plugin._get_image_from_reply = AsyncMock(return_value=reply_image)
        plugin._run_command_search = AsyncMock(return_value="搜索失败")
        event = FakeEvent(timeline, messages=[Reply(id="123")])

        yielded = [
            result async for result in plugin.search_image_cmd(event)
        ]

        self.assertEqual(yielded, ["搜索失败"])

    async def test_command_prefers_first_attachment_and_passes_strategies(
        self,
    ) -> None:
        timeline: list[tuple[str, object]] = []
        service = SimpleNamespace(
            get_available_strategies=lambda: ["saucenao", "ascii2d"],
            resolve_strategy_names=lambda _names: ([], []),
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        plugin._get_image_from_reply = AsyncMock()
        plugin._run_command_search = AsyncMock(return_value=None)
        first_image = Image(file="base64://first")
        second_image = Image(file="base64://second")
        event = FakeEvent(
            timeline,
            message_str="搜图 sauce,2d",
            messages=[Reply(id="123"), first_image, second_image],
        )

        yielded = [
            result async for result in plugin.search_image_cmd(event)
        ]

        self.assertEqual(yielded, [])
        plugin._run_command_search.assert_awaited_once_with(
            event,
            first_image,
            ["sauce", "2d"],
        )
        plugin._get_image_from_reply.assert_not_awaited()

    async def test_runner_prefers_http_file_over_non_http_url(self) -> None:
        timeline: list[tuple[str, object]] = []
        service = RecordingService(timeline, ExplorationResult())
        plugin = self.make_plugin(service)
        plugin._send_search_results = AsyncMock()
        image = Image(
            file="https://image.example/from-file.jpg",
            url="file:///local-url.jpg",
        )
        convert_image = AsyncMock()
        event = FakeEvent(timeline)

        with patch(
            "astrbot_plugin_imgexploration.main.get_http_image_url",
            new=convert_image,
        ):
            terminal_message = await plugin._run_command_search(
                event,
                image,
                None,
            )

        self.assertEqual(
            timeline,
            [
                ("send", "搜索中..."),
                ("explore", ("https://image.example/from-file.jpg", None)),
            ],
        )
        self.assertEqual(
            terminal_message,
            "未找到相关图片来源，请尝试更换图片或稍后重试。",
        )
        convert_image.assert_not_awaited()

    async def test_runner_tries_non_http_url_and_file_independently(self) -> None:
        timeline: list[tuple[str, object]] = []
        service = RecordingService(timeline, ExplorationResult())
        plugin = self.make_plugin(service)
        plugin._send_search_results = AsyncMock()
        image = Image(file="base64://file", url="file:///local-url.jpg")
        convert_image = AsyncMock(
            side_effect=[None, "https://image.example/uploaded.jpg"]
        )
        event = FakeEvent(timeline)

        with patch(
            "astrbot_plugin_imgexploration.main.get_http_image_url",
            new=convert_image,
        ):
            terminal_message = await plugin._run_command_search(
                event,
                image,
                ["saucenao"],
            )

        self.assertEqual(
            convert_image.await_args_list,
            [call("file:///local-url.jpg"), call("base64://file")],
        )
        self.assertEqual(
            timeline,
            [
                ("send", "搜索中..."),
                (
                    "explore",
                    (
                        "https://image.example/uploaded.jpg",
                        ["saucenao"],
                    ),
                ),
            ],
        )
        self.assertEqual(
            terminal_message,
            "未找到相关图片来源，请尝试更换图片或稍后重试。",
        )

    async def test_reply_chain_is_used_before_onebot_fallback(self) -> None:
        timeline: list[tuple[str, object]] = []
        event = FakeEvent(timeline)
        reply_image = Image(file="https://image.example/reply-chain.jpg")
        reply = Reply(id="123", chain=[reply_image])

        with patch(
            "astrbot_plugin_imgexploration.main.get_bot_api"
        ) as get_bot_api:
            image = await ImgExplorationPlugin._get_image_from_reply(
                event,
                reply,
            )

        self.assertIs(image, reply.chain[0])
        get_bot_api.assert_not_called()

    async def test_reply_falls_back_to_onebot_get_msg(self) -> None:
        timeline: list[tuple[str, object]] = []
        event = FakeEvent(timeline)
        reply = Reply(id="123", chain=[])
        bot = SimpleNamespace(
            call_action=AsyncMock(
                return_value={
                    "message": [
                        {
                            "type": "image",
                            "data": {
                                "url": "https://image.example/onebot.jpg",
                                "file": "onebot-file-token",
                            },
                        }
                    ]
                }
            )
        )

        with patch(
            "astrbot_plugin_imgexploration.main.get_bot_api",
            return_value=bot,
        ):
            image = await ImgExplorationPlugin._get_image_from_reply(
                event,
                reply,
            )

        self.assertIsInstance(image, Image)
        assert image is not None
        self.assertEqual(image.url, "https://image.example/onebot.jpg")
        self.assertEqual(image.file, "onebot-file-token")
        bot.call_action.assert_awaited_once_with("get_msg", message_id=123)


if __name__ == "__main__":
    unittest.main()
