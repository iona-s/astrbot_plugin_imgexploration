from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

from astrbot.core.message.components import At, Image, Plain, Reply
from astrbot.core.star.filter.platform_adapter_type import PlatformAdapterType
from astrbot.core.star.star_handler import star_handlers_registry
from astrbot_plugin_imgexploration.core.models import (
    ExplorationResult,
    SearchResultItem,
)
from astrbot_plugin_imgexploration.main import ImgExplorationPlugin

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

        with (
            patch(
                "astrbot_plugin_imgexploration.core.image_sources.get_image_from_reply",
                new=AsyncMock(return_value=None),
            ) as get_image_from_reply,
            patch.object(
                plugin._image_wait,
                "create",
                wraps=plugin._image_wait.create,
            ) as create_wait,
        ):
            yielded = [result async for result in plugin.search_image_cmd(event)]

        self.assertEqual(yielded, ["回复消息中未找到图片"])
        create_wait.assert_not_awaited()
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


class AutoMentionCommandTests(PluginTestCase):
    @staticmethod
    def _reply_command_components(
        command_text: str = "/搜图",
        *,
        reply_sender_id: str | int = "42",
        mention_qq: str | int = "42",
    ) -> list[object]:
        return [
            Reply(id="123", sender_id=reply_sender_id),
            At(qq=mention_qq),
            Plain(command_text),
        ]

    async def test_auto_mention_handler_rejects_invalid_shapes(self) -> None:
        rejected_cases = [
            (
                "already activated command",
                self._reply_command_components(),
                True,
            ),
            (
                "mismatched mention id",
                self._reply_command_components(mention_qq="43"),
                False,
            ),
            (
                "empty reply sender id",
                self._reply_command_components(reply_sender_id=0, mention_qq=0),
                False,
            ),
            (
                "reordered components",
                [
                    At(qq="42"),
                    Reply(id="123", sender_id="42"),
                    Plain("/搜图"),
                ],
                False,
            ),
            (
                "extra component",
                [
                    *self._reply_command_components(),
                    Plain("附加文本"),
                ],
                False,
            ),
            (
                "missing reply",
                [At(qq="42"), Plain("/搜图")],
                False,
            ),
            (
                "missing slash",
                self._reply_command_components("搜图"),
                False,
            ),
            (
                "similar command name",
                self._reply_command_components("/搜图片"),
                False,
            ),
            (
                "whitespace without arguments",
                self._reply_command_components("/搜图   "),
                False,
            ),
        ]
        for label, messages, is_command in rejected_cases:
            with self.subTest(label=label):
                event = FakeEvent(
                    [],
                    message_str="@member(42) /搜图",
                    messages=messages,
                    is_command=is_command,
                )
                plugin = self.make_plugin(SimpleNamespace())
                plugin._run_command_search = AsyncMock(return_value=None)

                yielded = [
                    result
                    async for result in plugin.search_image_auto_mention_cmd(event)
                ]

                self.assertEqual(yielded, [])
                self.assertFalse(event.is_stopped())
                plugin._run_command_search.assert_not_awaited()

    def test_auto_mention_handler_is_aiocqhttp_only_and_priority_two(self) -> None:
        handler_full_name = (
            f"{ImgExplorationPlugin.search_image_auto_mention_cmd.__module__}_"
            f"{ImgExplorationPlugin.search_image_auto_mention_cmd.__name__}"
        )
        handler = star_handlers_registry.get_handler_by_full_name(handler_full_name)

        self.assertIsNotNone(handler)
        assert handler is not None
        self.assertEqual(handler.extras_configs["priority"], 2)
        self.assertEqual(len(handler.event_filters), 1)
        platform_filter = handler.event_filters[0]
        self.assertEqual(platform_filter.platform_type, PlatformAdapterType.AIOCQHTTP)

        aiocqhttp_event = FakeEvent([], platform_name="aiocqhttp")
        other_adapter_event = FakeEvent([], platform_name="qq_official")
        self.assertTrue(platform_filter.filter(aiocqhttp_event, {}))
        self.assertFalse(platform_filter.filter(other_adapter_event, {}))

    async def test_auto_mention_searches_once_and_stops_before_reply_lookup(
        self,
    ) -> None:
        timeline: list[tuple[str, object]] = []
        service = SimpleNamespace(
            get_available_strategies=lambda: ["saucenao", "ascii2d"],
            resolve_strategy_names=lambda _names: ([], []),
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        reply_image = Image(file="https://image.example/reply.jpg")
        event = FakeEvent(
            timeline,
            message_str="@member(42) /搜图 sauce,2d",
            messages=self._reply_command_components("/搜图 sauce,2d"),
            is_command=False,
        )

        async def resolve_reply(
            _event: FakeEvent,
            _reply: Reply,
        ) -> Image:
            timeline.append(("reply_lookup", None))
            return reply_image

        async def run_search(
            *_args: object,
            **_kwargs: object,
        ) -> str:
            timeline.append(("search", None))
            return "搜索失败"

        plugin._run_command_search = AsyncMock(side_effect=run_search)
        get_image_from_reply = AsyncMock(side_effect=resolve_reply)
        with patch(
            "astrbot_plugin_imgexploration.core.image_sources.get_image_from_reply",
            new=get_image_from_reply,
        ):
            yielded = [
                result async for result in plugin.search_image_auto_mention_cmd(event)
            ]

        self.assertEqual(yielded, ["搜索失败"])
        self.assertTrue(event.is_stopped())
        self.assertEqual(
            timeline,
            [("stop", None), ("reply_lookup", None), ("search", None)],
        )
        get_image_from_reply.assert_awaited_once_with(event, event.get_messages()[0])
        plugin._run_command_search.assert_awaited_once_with(
            event,
            reply_image,
            ["sauce", "2d"],
        )

    async def test_auto_mention_reply_without_image_does_not_create_wait(self) -> None:
        timeline: list[tuple[str, object]] = []
        service = SimpleNamespace(
            get_available_strategies=lambda: ["saucenao"],
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        event = FakeEvent(
            timeline,
            messages=self._reply_command_components(),
            is_command=False,
        )
        plugin._run_command_search = AsyncMock(return_value=None)

        with (
            patch(
                "astrbot_plugin_imgexploration.core.image_sources.get_image_from_reply",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                plugin._image_wait,
                "create",
                wraps=plugin._image_wait.create,
            ) as create_wait,
        ):
            yielded = [
                result async for result in plugin.search_image_auto_mention_cmd(event)
            ]

        self.assertEqual(yielded, ["回复消息中未找到图片"])
        self.assertTrue(event.is_stopped())
        create_wait.assert_not_awaited()
        plugin._run_command_search.assert_not_awaited()

    async def test_auto_mention_handler_ignores_already_activated_commands(
        self,
    ) -> None:
        timeline: list[tuple[str, object]] = []
        plugin = self.make_plugin(SimpleNamespace())
        plugin._run_command_search = AsyncMock(return_value=None)
        event = FakeEvent(
            timeline,
            messages=self._reply_command_components(),
            is_command=True,
        )

        yielded = [
            result async for result in plugin.search_image_auto_mention_cmd(event)
        ]

        self.assertEqual(yielded, [])
        self.assertFalse(event.is_stopped())
        plugin._run_command_search.assert_not_awaited()


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

    async def test_reports_all_providers_failed(self) -> None:
        timeline: list[tuple[str, object]] = []
        service = RecordingService(
            timeline,
            ExplorationResult(
                attempted_providers=["SauceNAO", "GoogleLens"],
                failed_providers=["SauceNAO", "GoogleLens"],
            ),
        )
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
            "搜索服务暂时不可用，请稍后重试。",
        )
        send_results.assert_not_awaited()

    async def test_sends_provider_notice_before_other_strategy_results(self) -> None:
        timeline: list[tuple[str, object]] = []
        notice = "[SauceNAO]返回结果均低于40%相似度阈值"
        item = SearchResultItem(
            title="Lens Result",
            url="https://result.example/lens",
            source="Google Lens",
        )
        service = RecordingService(
            timeline,
            ExplorationResult(
                items=[item],
                attempted_providers=["SauceNAO", "Google Lens"],
                user_notices=[notice],
            ),
        )
        plugin = self.make_plugin(service)
        event = FakeEvent(timeline)

        async def send_results(_event: object, items: list[SearchResultItem]) -> None:
            timeline.append(("results", items))

        with (
            patch(
                "astrbot_plugin_imgexploration.main.get_http_image_url",
                new=AsyncMock(return_value="https://image.example/source.jpg"),
            ),
            patch(
                "astrbot_plugin_imgexploration.core.result_sender.send_search_results",
                new=send_results,
            ),
        ):
            terminal_message = await plugin._run_command_search(
                event,
                "source",
                None,
            )

        self.assertIsNone(terminal_message)
        self.assertEqual(
            timeline,
            [
                ("send", "搜索中..."),
                ("explore", ("https://image.example/source.jpg", None)),
                ("send", notice),
                ("results", [item]),
            ],
        )

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
