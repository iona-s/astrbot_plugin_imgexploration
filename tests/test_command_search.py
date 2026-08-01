from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

from astrbot.core.message.components import Image, Reply
from astrbot_plugin_imgexploration.core.models import (
    ExplorationResult,
    SearchResultItem,
)

from .helpers import FakeEvent, PluginTestCase


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


class CommandHandlerTests(PluginTestCase):
    async def test_command_delegates_reply_and_yields_runner_message(self) -> None:
        timeline: list[tuple[str, object]] = []
        service = SimpleNamespace(
            get_available_strategies=lambda: ["saucenao"],
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        reply = Reply(id="123")
        reply_image = Image(file="https://image.example/reply.jpg")
        plugin._run_command_search = AsyncMock(return_value="搜索失败")
        event = FakeEvent(timeline, messages=[reply])

        with patch(
            "astrbot_plugin_imgexploration.core.image_sources.get_image_from_reply",
            new=AsyncMock(return_value=reply_image),
        ) as get_image_from_reply:
            yielded = [result async for result in plugin.search_image_cmd(event)]

        self.assertEqual(yielded, ["搜索失败"])
        get_image_from_reply.assert_awaited_once_with(event, reply)
        plugin._run_command_search.assert_awaited_once_with(
            event,
            reply_image,
            None,
        )

    async def test_command_reply_without_image_does_not_enter_wait(self) -> None:
        timeline: list[tuple[str, object]] = []
        service = SimpleNamespace(
            get_available_strategies=lambda: ["saucenao"],
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        plugin._run_command_search = AsyncMock(return_value=None)
        reply = Reply(id="123")
        event = FakeEvent(timeline, messages=[reply])

        with patch(
            "astrbot_plugin_imgexploration.core.image_sources.get_image_from_reply",
            new=AsyncMock(return_value=None),
        ) as get_image_from_reply:
            yielded = [result async for result in plugin.search_image_cmd(event)]

        self.assertEqual(yielded, ["回复消息中未找到图片"])
        self.assertEqual(plugin._image_wait_states, {})
        get_image_from_reply.assert_awaited_once_with(event, reply)
        plugin._run_command_search.assert_not_awaited()

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
        plugin._run_command_search = AsyncMock(return_value=None)
        first_image = Image(file="base64://first")
        second_image = Image(file="base64://second")
        event = FakeEvent(
            timeline,
            message_str="搜图 sauce,2d",
            messages=[Reply(id="123"), first_image, second_image],
        )

        with patch(
            "astrbot_plugin_imgexploration.core.image_sources.get_image_from_reply",
            new=AsyncMock(),
        ) as get_image_from_reply:
            yielded = [result async for result in plugin.search_image_cmd(event)]

        self.assertEqual(yielded, [])
        plugin._run_command_search.assert_awaited_once_with(
            event,
            first_image,
            ["sauce", "2d"],
        )
        get_image_from_reply.assert_not_awaited()


class CommandSearchRunnerTests(PluginTestCase):
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

        event = FakeEvent(timeline)

        with (
            patch(
                "astrbot_plugin_imgexploration.main.get_http_image_url",
                new=convert_image,
            ),
            patch(
                "astrbot_plugin_imgexploration.core.result_sender.send_search_results",
                new=send_results,
            ),
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
        event = FakeEvent(timeline)
        image = Image(file="invalid-file", url="invalid-url")
        convert_image = AsyncMock(return_value=None)

        with (
            patch(
                "astrbot_plugin_imgexploration.main.get_http_image_url",
                new=convert_image,
            ),
            patch(
                "astrbot_plugin_imgexploration.core.result_sender.send_search_results",
                new=AsyncMock(),
            ) as send_results,
        ):
            terminal_message = await plugin._run_command_search(
                event,
                image,
                None,
            )

        self.assertEqual(timeline, [("send", "搜索中...")])
        self.assertEqual(terminal_message, "获取图片失败")
        self.assertEqual(
            convert_image.await_args_list,
            [call("invalid-url"), call("invalid-file")],
        )
        send_results.assert_not_awaited()

    async def test_reports_empty_results_after_one_acknowledgement(self) -> None:
        timeline: list[tuple[str, object]] = []
        service = RecordingService(timeline, ExplorationResult())
        plugin = self.make_plugin(service)
        event = FakeEvent(timeline)

        with (
            patch(
                "astrbot_plugin_imgexploration.main.get_http_image_url",
                new=AsyncMock(return_value="https://image.example/source.jpg"),
            ),
            patch(
                "astrbot_plugin_imgexploration.core.result_sender.send_search_results",
                new=AsyncMock(),
            ) as send_results,
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
        send_results.assert_not_awaited()

    async def test_prefers_http_file_over_non_http_url(self) -> None:
        timeline: list[tuple[str, object]] = []
        service = RecordingService(timeline, ExplorationResult())
        plugin = self.make_plugin(service)
        image = Image(
            file="https://image.example/from-file.jpg",
            url="file:///local-url.jpg",
        )
        convert_image = AsyncMock()
        event = FakeEvent(
            timeline,
            raw_message={
                "message": [
                    {
                        "type": "image",
                        "data": {"url": "https://image.example/raw-source.jpg"},
                    }
                ]
            },
        )

        with (
            patch(
                "astrbot_plugin_imgexploration.main.get_http_image_url",
                new=convert_image,
            ),
            patch(
                "astrbot_plugin_imgexploration.core.result_sender.send_search_results",
                new=AsyncMock(),
            ),
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

    async def test_uses_raw_http_before_local_image_conversion(self) -> None:
        timeline: list[tuple[str, object]] = []
        service = RecordingService(timeline, ExplorationResult())
        plugin = self.make_plugin(service)
        raw_url = "https://image.example/raw-source.jpg?secret=signed-value"
        image = Image(
            file="local-file-token",
            url="file:///tmp/local-image.jpg",
        )
        event = FakeEvent(
            timeline,
            raw_message={
                "message": [
                    {
                        "type": "image",
                        "data": {"url": raw_url},
                    }
                ]
            },
        )
        convert_image = AsyncMock()

        with (
            patch(
                "astrbot_plugin_imgexploration.main.get_http_image_url",
                new=convert_image,
            ),
            patch(
                "astrbot_plugin_imgexploration.core.result_sender.send_search_results",
                new=AsyncMock(),
            ),
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
                ("explore", (raw_url, None)),
            ],
        )
        self.assertEqual(
            terminal_message,
            "未找到相关图片来源，请尝试更换图片或稍后重试。",
        )
        convert_image.assert_not_awaited()

    async def test_tries_non_http_url_and_file_independently(self) -> None:
        timeline: list[tuple[str, object]] = []
        service = RecordingService(timeline, ExplorationResult())
        plugin = self.make_plugin(service)
        image = Image(file="base64://file", url="file:///local-url.jpg")
        convert_image = AsyncMock(
            side_effect=[None, "https://image.example/uploaded.jpg"]
        )
        event = FakeEvent(timeline)

        with (
            patch(
                "astrbot_plugin_imgexploration.main.get_http_image_url",
                new=convert_image,
            ),
            patch(
                "astrbot_plugin_imgexploration.core.result_sender.send_search_results",
                new=AsyncMock(),
            ),
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
