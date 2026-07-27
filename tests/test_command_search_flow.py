from __future__ import annotations

import asyncio
import atexit
import json
import os
import sys
import tempfile
import unittest
from collections.abc import Awaitable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

TEST_ASTRBOT_ROOT = tempfile.TemporaryDirectory(prefix="astrbot-imgexploration-tests-")
atexit.register(TEST_ASTRBOT_ROOT.cleanup)
os.environ["ASTRBOT_ROOT"] = TEST_ASTRBOT_ROOT.name

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASTRBOT_ROOT = PLUGIN_ROOT.parents[2]
PLUGIN_PARENT = PLUGIN_ROOT.parent
for import_root in (ASTRBOT_ROOT, PLUGIN_PARENT):
    import_root_str = str(import_root)
    if import_root_str not in sys.path:
        sys.path.insert(0, import_root_str)

from astrbot_plugin_imgexploration.image_context import (  # noqa: E402
    ImageContextManager,
)
from astrbot_plugin_imgexploration.main import (  # noqa: E402
    ImgExplorationPlugin,
)
from astrbot_plugin_imgexploration.models import (  # noqa: E402
    ExplorationResult,
    SearchResultItem,
)

from astrbot.core.message.components import Image, Reply  # noqa: E402


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
        plugin._image_wait_timeout_seconds = 60
        plugin._image_wait_states = {}
        plugin._image_wait_lock = asyncio.Lock()
        plugin._image_wait_clock = Mock(return_value=0.0)
        return plugin

    async def assert_async_iteration_stops(
        self,
        awaitable: Awaitable[object],
    ) -> None:
        with self.assertRaises(StopAsyncIteration):
            await awaitable

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

        yielded = [result async for result in plugin.search_image_cmd(event)]

        self.assertEqual(yielded, [])
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
        plugin._get_image_from_reply = AsyncMock(return_value=None)
        plugin._run_command_search = AsyncMock(return_value=None)
        reply = Reply(id="123")
        event = FakeEvent(timeline, messages=[reply])

        yielded = [result async for result in plugin.search_image_cmd(event)]

        self.assertEqual(yielded, ["回复消息中未找到图片"])
        self.assertEqual(plugin._image_wait_states, {})
        plugin._get_image_from_reply.assert_awaited_once_with(
            event,
            reply,
        )
        plugin._run_command_search.assert_not_awaited()

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

        yielded = [result async for result in plugin.search_image_cmd(event)]

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

        yielded = [result async for result in plugin.search_image_cmd(event)]

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

        with patch("astrbot_plugin_imgexploration.main.get_bot_api") as get_bot_api:
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

    def test_normalizes_image_wait_timeout_to_supported_range(self) -> None:
        normalize = ImgExplorationPlugin._normalize_image_wait_timeout

        self.assertEqual(normalize(60), 60)
        self.assertEqual(normalize(29), 30)
        self.assertEqual(normalize(121), 120)
        self.assertEqual(normalize("90"), 90)
        self.assertEqual(normalize("invalid"), 60)
        self.assertEqual(normalize(True), 60)

    def test_image_wait_timeout_schema_defaults_and_range(self) -> None:
        schema = json.loads(
            (PLUGIN_ROOT / "_conf_schema.json").read_text(encoding="utf-8")
        )
        setting = schema["command"]["items"]["image_wait_timeout_seconds"]

        self.assertEqual(setting["default"], 60)
        self.assertEqual(setting["slider"]["min"], 30)
        self.assertEqual(setting["slider"]["max"], 120)

    async def test_command_without_image_enters_wait_with_strategies(self) -> None:
        timeline: list[tuple[str, object]] = []
        service = SimpleNamespace(
            get_available_strategies=lambda: ["saucenao"],
            resolve_strategy_names=lambda _names: ([], []),
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        plugin._image_wait_clock = Mock(return_value=100.0)
        event = FakeEvent(
            timeline,
            message_str="搜图 saucenao",
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )

        handler = plugin.search_image_cmd(event)
        first_result = await anext(handler)

        self.assertEqual(
            first_result,
            "请在60秒内发送图片。",
        )
        state = plugin._image_wait_states[("test:group:100", "user-a")]
        self.assertEqual(state.strategy_names, ["saucenao"])
        self.assertEqual(state.expires_at, 160.0)
        self.assertFalse(state.future.done())

        await plugin._clear_image_wait(event)
        await self.assert_async_iteration_stops(anext(handler))

    async def test_wait_times_out_without_another_message(self) -> None:
        timeline: list[tuple[str, object]] = []
        service = SimpleNamespace(
            get_available_strategies=lambda: ["saucenao"],
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        plugin._image_wait_timeout_seconds = 0.01
        plugin._image_wait_clock = asyncio.get_running_loop().time
        plugin._run_command_search = AsyncMock(return_value=None)
        event = FakeEvent(timeline)
        handler = plugin.search_image_cmd(event)

        await anext(handler)
        timeout_result = await asyncio.wait_for(anext(handler), timeout=0.5)

        self.assertEqual(timeout_result, "搜图等待已超时")
        self.assertEqual(plugin._image_wait_states, {})
        plugin._run_command_search.assert_not_awaited()
        await self.assert_async_iteration_stops(anext(handler))

    async def test_repeated_command_keeps_existing_wait_and_reports_mode(
        self,
    ) -> None:
        timeline: list[tuple[str, object]] = []
        service = SimpleNamespace(
            get_available_strategies=lambda: ["saucenao", "ascii2d"],
            resolve_strategy_names=lambda _names: ([], []),
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        plugin._image_wait_clock = Mock(return_value=100.0)
        first_event = FakeEvent(
            timeline,
            message_str="搜图 saucenao",
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )
        second_event = FakeEvent(
            timeline,
            message_str="搜图 ascii2d",
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )

        first_handler = plugin.search_image_cmd(first_event)
        first_result = await anext(first_handler)
        first_state = plugin._image_wait_states[("test:group:100", "user-a")]

        plugin._image_wait_clock = Mock(return_value=120.0)
        second_handler = plugin.search_image_cmd(second_event)
        second_result = await anext(second_handler)

        self.assertEqual(
            first_result,
            "请在60秒内发送图片。",
        )
        self.assertEqual(
            second_result,
            "当前已进入搜索模式，请直接发送图片",
        )
        self.assertFalse(first_state.future.done())
        await self.assert_async_iteration_stops(anext(second_handler))
        self.assertEqual(len(plugin._image_wait_states), 1)
        state = plugin._image_wait_states[("test:group:100", "user-a")]
        self.assertIs(state, first_state)
        self.assertEqual(state.strategy_names, ["saucenao"])
        self.assertEqual(state.expires_at, 160.0)

        await plugin._clear_image_wait(first_event)
        await self.assert_async_iteration_stops(anext(first_handler))

    async def test_wait_isolated_by_session_and_sender_and_ignores_text(
        self,
    ) -> None:
        timeline: list[tuple[str, object]] = []
        service = SimpleNamespace(
            get_available_strategies=lambda: ["saucenao"],
            resolve_strategy_names=lambda _names: ([], []),
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        plugin._image_wait_clock = Mock(return_value=100.0)
        wait_event = FakeEvent(
            timeline,
            message_str="搜图 saucenao",
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )
        handler = plugin.search_image_cmd(wait_event)
        await anext(handler)
        plugin._image_wait_clock = Mock(return_value=110.0)
        plugin._run_command_search = AsyncMock(return_value=None)
        wait_completion = asyncio.create_task(anext(handler))
        await asyncio.sleep(0)

        other_member_image = Image(file="base64://other-member")
        other_member_event = FakeEvent(
            timeline,
            message_str="",
            messages=[other_member_image],
            unified_msg_origin="test:group:100",
            sender_id="user-b",
            is_command=False,
        )
        other_session_image = Image(file="base64://other-session")
        other_session_event = FakeEvent(
            timeline,
            message_str="",
            messages=[other_session_image],
            unified_msg_origin="test:group:200",
            sender_id="user-a",
            is_command=False,
        )
        text_event = FakeEvent(
            timeline,
            message_str="仍在等待",
            unified_msg_origin="test:group:100",
            sender_id="user-a",
            is_command=False,
        )
        matching_image = Image(file="base64://matching")
        matching_event = FakeEvent(
            timeline,
            message_str="",
            messages=[matching_image],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
            is_command=False,
        )
        image_context = SimpleNamespace(add_image=Mock())

        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager",
            return_value=image_context,
        ):
            await plugin.on_message(other_member_event)
            await plugin.on_message(other_session_event)
            await plugin.on_message(text_event)
            self.assertIn(
                ("test:group:100", "user-a"),
                plugin._image_wait_states,
            )
            await plugin.on_message(matching_event)

        await self.assert_async_iteration_stops(wait_completion)
        plugin._run_command_search.assert_awaited_once_with(
            matching_event,
            matching_image,
            ["saucenao"],
        )
        self.assertNotIn(
            ("test:group:100", "user-a"),
            plugin._image_wait_states,
        )

    async def test_capture_uses_raw_http_when_component_is_local(
        self,
    ) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        image_context = SimpleNamespace(add_image=Mock())
        raw_url = "https://image.example/source.jpg?secret=signed-value"
        event = FakeEvent(
            [],
            message_str="",
            messages=[
                Image(
                    file="local-file-token",
                    url="file:///tmp/local-image.jpg",
                )
            ],
            is_command=False,
            raw_message={
                "message": [
                    {
                        "type": "image",
                        "data": {"url": raw_url},
                    }
                ]
            },
        )

        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager",
            return_value=image_context,
        ):
            await plugin.on_message(event)

        image_context.add_image.assert_called_once_with(
            event,
            raw_url,
            message_id="message-1",
            sender_id="user-1",
        )

    async def test_capture_uses_component_http_url_without_raw_event(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        image_context = SimpleNamespace(add_image=Mock())
        image_url = "https://image.example/source.jpg?secret=signed-value"
        event = FakeEvent(
            [],
            message_str="",
            messages=[
                Image(
                    file="local-file-token",
                    url=image_url,
                )
            ],
            sender_id="user-a",
            is_command=False,
            message_id="message-a",
        )

        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager",
            return_value=image_context,
        ):
            await plugin.on_message(event)

        image_context.add_image.assert_called_once_with(
            event,
            image_url,
            message_id="message-a",
            sender_id="user-a",
        )

    async def test_capture_uses_raw_http_without_matching_component(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        image_context = SimpleNamespace(add_image=Mock())
        raw_url = "https://image.example/source.jpg?secret=signed-value"
        event = FakeEvent(
            [],
            message_str="",
            messages=[
                SimpleNamespace(
                    file="local-file-token",
                    url=raw_url,
                )
            ],
            is_command=False,
            raw_message=SimpleNamespace(
                message=[
                    {
                        "type": "image",
                        "data": {"url": raw_url},
                    }
                ]
            ),
        )

        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager",
            return_value=image_context,
        ):
            await plugin.on_message(event)

        image_context.add_image.assert_called_once_with(
            event,
            raw_url,
            message_id="message-1",
            sender_id="user-1",
        )

    async def test_capture_checks_component_url_and_file_independently(
        self,
    ) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        image_context = SimpleNamespace(add_image=Mock())
        file_url = "https://image.example/from-file.jpg"
        event = FakeEvent(
            [],
            message_str="",
            messages=[
                Image(
                    file=file_url,
                    url="file:///tmp/local-image.jpg",
                )
            ],
            is_command=False,
            raw_message={
                "message": [
                    {
                        "type": "image",
                        "data": {"url": file_url},
                    }
                ]
            },
        )

        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager",
            return_value=image_context,
        ):
            await plugin.on_message(event)

        image_context.add_image.assert_called_once_with(
            event,
            file_url,
            message_id="message-1",
            sender_id="user-1",
        )

    async def test_capture_preserves_candidate_order_and_deduplicates(
        self,
    ) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        image_context = SimpleNamespace(add_image=Mock())
        component_url = "https://image.example/component-url.jpg"
        component_file = "https://image.example/component-file.jpg"
        raw_url = "https://image.example/raw-fallback.jpg"
        event = FakeEvent(
            [],
            message_str="",
            messages=[
                Image(
                    file=component_file,
                    url=component_url,
                ),
                Image(
                    file="local-file-token",
                    url="file:///tmp/local-image.jpg",
                ),
            ],
            is_command=False,
            raw_message={
                "message": [
                    {
                        "type": "image",
                        "data": {"url": component_url},
                    },
                    {
                        "type": "image",
                        "data": {"url": raw_url},
                    },
                    {
                        "type": "image",
                        "data": {"url": component_file},
                    },
                ]
            },
        )

        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager",
            return_value=image_context,
        ):
            await plugin.on_message(event)

        self.assertEqual(
            image_context.add_image.call_args_list,
            [
                call(
                    event,
                    component_url,
                    message_id="message-1",
                    sender_id="user-1",
                ),
                call(
                    event,
                    component_file,
                    message_id="message-1",
                    sender_id="user-1",
                ),
                call(
                    event,
                    raw_url,
                    message_id="message-1",
                    sender_id="user-1",
                ),
            ],
        )

    def test_raw_image_extraction_ignores_malformed_shapes(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())

        for raw_message in (
            None,
            {},
            {"message": "not-a-message-chain"},
        ):
            with self.subTest(raw_message=raw_message):
                event = FakeEvent([], raw_message=raw_message)
                self.assertEqual(plugin._get_raw_image_urls(event), [])

        event = FakeEvent(
            [],
            raw_message=SimpleNamespace(
                message=[
                    None,
                    {"type": "text", "data": {"url": "https://example/text"}},
                    {"type": "image", "data": None},
                    {"type": "image", "data": {"url": 123}},
                    {"type": "image", "data": {"url": "file:///tmp/image.jpg"}},
                ]
            ),
        )
        self.assertEqual(plugin._get_raw_image_urls(event), [])

    def test_image_context_log_omits_url(self) -> None:
        image_context = ImageContextManager(isolation_mode="global")
        image_url = "https://image.example/source.jpg?secret=signed-value"

        with patch(
            "astrbot_plugin_imgexploration.image_context.logger.debug"
        ) as debug_log:
            image_context.add_image(
                SimpleNamespace(),
                image_url,
                message_id="message-a",
                sender_id="user-a",
            )

        debug_log.assert_called_once()
        log_message = str(debug_log.call_args.args[0])
        self.assertIn("image_id=", log_message)
        self.assertNotIn("message-a", log_message)
        self.assertNotIn("user-a", log_message)
        self.assertNotIn(image_url, log_message)
        self.assertNotIn("signed-value", log_message)

    async def test_late_image_completes_wait_with_one_timeout(self) -> None:
        timeline: list[tuple[str, object]] = []
        service = SimpleNamespace(
            get_available_strategies=lambda: ["saucenao"],
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        plugin._image_wait_clock = Mock(return_value=100.0)
        wait_event = FakeEvent(
            timeline,
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )
        handler = plugin.search_image_cmd(wait_event)
        await anext(handler)
        plugin._image_wait_clock = Mock(return_value=161.0)
        plugin._run_command_search = AsyncMock(return_value=None)
        image_event = FakeEvent(
            timeline,
            message_str="",
            messages=[Image(file="base64://late")],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
            is_command=False,
        )

        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager",
            return_value=SimpleNamespace(add_image=Mock()),
        ):
            await plugin.on_message(image_event)

        timeout_result = await anext(handler)

        self.assertEqual(timeout_result, "搜图等待已超时")
        self.assertEqual(timeline, [])
        plugin._run_command_search.assert_not_awaited()
        self.assertEqual(plugin._image_wait_states, {})
        await self.assert_async_iteration_stops(anext(handler))

    async def test_concurrent_images_consume_wait_once(self) -> None:
        timeline: list[tuple[str, object]] = []
        service = SimpleNamespace(
            get_available_strategies=lambda: ["saucenao"],
            resolve_strategy_names=lambda _names: ([], []),
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        plugin._image_wait_clock = Mock(return_value=100.0)
        wait_event = FakeEvent(
            timeline,
            message_str="搜图 saucenao",
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )
        handler = plugin.search_image_cmd(wait_event)
        await anext(handler)
        plugin._image_wait_clock = Mock(return_value=110.0)

        async def run_search(*_args: object) -> None:
            await asyncio.sleep(0)

        plugin._run_command_search = AsyncMock(side_effect=run_search)
        first_event = FakeEvent(
            timeline,
            message_str="",
            messages=[Image(file="base64://first")],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
            is_command=False,
        )
        second_event = FakeEvent(
            timeline,
            message_str="",
            messages=[Image(file="base64://second")],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
            is_command=False,
        )
        wait_completion = asyncio.create_task(anext(handler))
        await asyncio.sleep(0)

        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager",
            return_value=SimpleNamespace(add_image=Mock()),
        ):
            await asyncio.gather(
                plugin.on_message(first_event),
                plugin.on_message(second_event),
            )

        await self.assert_async_iteration_stops(wait_completion)
        self.assertEqual(plugin._run_command_search.await_count, 1)
        self.assertEqual(plugin._image_wait_states, {})

    async def test_command_image_does_not_consume_wait_twice(self) -> None:
        timeline: list[tuple[str, object]] = []
        service = SimpleNamespace(
            get_available_strategies=lambda: ["saucenao"],
            resolve_strategy_names=lambda _names: ([], []),
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        plugin._image_wait_clock = Mock(return_value=100.0)
        wait_event = FakeEvent(
            timeline,
            message_str="搜图 saucenao",
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )
        wait_handler = plugin.search_image_cmd(wait_event)
        await anext(wait_handler)
        plugin._image_wait_clock = Mock(return_value=110.0)
        plugin._run_command_search = AsyncMock(return_value=None)
        wait_completion = asyncio.create_task(anext(wait_handler))
        await asyncio.sleep(0)
        image = Image(file="base64://command-image")
        command_event = FakeEvent(
            timeline,
            message_str="搜图",
            messages=[image],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
            is_command=True,
        )

        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager",
            return_value=SimpleNamespace(add_image=Mock()),
        ):
            await plugin.on_message(command_event)

        plugin._run_command_search.assert_not_awaited()
        self.assertIn(
            ("test:group:100", "user-a"),
            plugin._image_wait_states,
        )

        yielded = [result async for result in plugin.search_image_cmd(command_event)]

        self.assertEqual(yielded, [])
        plugin._run_command_search.assert_awaited_once_with(
            command_event,
            image,
            None,
        )
        self.assertNotIn(
            ("test:group:100", "user-a"),
            plugin._image_wait_states,
        )
        await self.assert_async_iteration_stops(wait_completion)

    async def test_waited_image_sends_terminal_message_through_image_event(
        self,
    ) -> None:
        timeline: list[tuple[str, object]] = []
        service = SimpleNamespace(
            get_available_strategies=lambda: ["saucenao"],
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        plugin._image_wait_clock = Mock(return_value=100.0)
        plugin._run_command_search = AsyncMock(return_value="获取图片失败")
        wait_event = FakeEvent(
            timeline,
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )
        image = Image(file="base64://matching")
        image_event = FakeEvent(
            timeline,
            message_str="",
            messages=[image],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
            is_command=False,
        )
        handler = plugin.search_image_cmd(wait_event)
        await anext(handler)
        plugin._image_wait_clock = Mock(return_value=110.0)
        wait_completion = asyncio.create_task(anext(handler))
        await asyncio.sleep(0)

        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager",
            return_value=SimpleNamespace(add_image=Mock()),
        ):
            await plugin.on_message(image_event)

        await self.assert_async_iteration_stops(wait_completion)
        self.assertEqual(timeline, [("send", "获取图片失败")])
        plugin._run_command_search.assert_awaited_once_with(
            image_event,
            image,
            None,
        )

    async def test_closing_wait_handler_clears_current_state(self) -> None:
        timeline: list[tuple[str, object]] = []
        service = SimpleNamespace(
            get_available_strategies=lambda: ["saucenao"],
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        plugin._image_wait_clock = Mock(return_value=100.0)
        event = FakeEvent(timeline)
        handler = plugin.search_image_cmd(event)

        await anext(handler)
        state = plugin._image_wait_states[("test:group:1", "user-1")]
        await handler.aclose()

        self.assertEqual(plugin._image_wait_states, {})
        self.assertTrue(state.future.done())
        self.assertEqual(state.future.result().value, "cancelled")

    async def test_cancelling_active_wait_clears_current_state(self) -> None:
        timeline: list[tuple[str, object]] = []
        service = SimpleNamespace(
            get_available_strategies=lambda: ["saucenao"],
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        plugin._image_wait_clock = Mock(return_value=100.0)
        event = FakeEvent(timeline)
        handler = plugin.search_image_cmd(event)

        await anext(handler)
        state = plugin._image_wait_states[("test:group:1", "user-1")]
        wait_task = asyncio.create_task(anext(handler))
        await asyncio.sleep(0)
        wait_task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await wait_task
        self.assertEqual(plugin._image_wait_states, {})
        self.assertTrue(state.future.done())
        self.assertEqual(state.future.result().value, "cancelled")
        await handler.aclose()

    async def test_setting_wait_opportunistically_cleans_expired_entries(
        self,
    ) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        plugin._image_wait_clock = Mock(side_effect=[100.0, 200.0])
        expired_event = FakeEvent(
            [],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )
        current_event = FakeEvent(
            [],
            unified_msg_origin="test:group:200",
            sender_id="user-b",
        )

        expired_state = await plugin._set_image_wait(expired_event, None)
        current_state = await plugin._set_image_wait(current_event, ["ascii2d"])

        self.assertNotIn(
            ("test:group:100", "user-a"),
            plugin._image_wait_states,
        )
        self.assertIn(
            ("test:group:200", "user-b"),
            plugin._image_wait_states,
        )
        self.assertTrue(expired_state.future.done())
        self.assertEqual(expired_state.future.result().value, "timed_out")

        plugin._image_wait_clock = Mock(return_value=200.0)
        await plugin._clear_image_wait(current_event)
        self.assertTrue(current_state.future.done())

    async def test_terminate_clears_wait_states(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        plugin._image_wait_clock = Mock(return_value=100.0)
        state = await plugin._set_image_wait(FakeEvent([]), None)
        plugin.strategies = []
        plugin._unregister_llm_tools = Mock()

        with patch(
            "astrbot_plugin_imgexploration.main.close_aiohttp_session",
            new=AsyncMock(),
        ) as close_session:
            await plugin.terminate()

        self.assertEqual(plugin._image_wait_states, {})
        self.assertTrue(state.future.done())
        self.assertEqual(state.future.result().value, "cancelled")
        plugin._unregister_llm_tools.assert_called_once_with()
        close_session.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
