from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from astrbot.core.message.components import Image
from astrbot.core.star.star_handler import star_handlers_registry
from astrbot_plugin_imgexploration.core.image_wait import (
    ImageWaitCoordinator,
    ImageWaitOutcome,
)
from astrbot_plugin_imgexploration.main import ImgExplorationPlugin

from .helpers import FakeEvent, PluginTestCase


class _ImageWaitPluginTestCase(PluginTestCase):
    def make_wait_plugin(
        self,
        *,
        timeout_seconds: float = 60,
        clock: Mock | None = None,
    ) -> ImgExplorationPlugin:
        service = SimpleNamespace(
            get_available_strategies=lambda: ["saucenao", "ascii2d"],
            resolve_strategy_names=lambda _names: ([], []),
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        plugin._image_wait = ImageWaitCoordinator(
            timeout_seconds,
            clock=clock or Mock(return_value=0.0),
        )
        return plugin

    def mock_image_context(self) -> Any:
        return patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager",
            return_value=SimpleNamespace(add_image=Mock()),
        )

    async def assert_async_iteration_stops(
        self,
        awaitable: Awaitable[object],
    ) -> None:
        with self.assertRaises(StopAsyncIteration):
            await awaitable


class ImageWaitFlowTests(_ImageWaitPluginTestCase):
    def test_wait_listener_runs_before_normal_priority_handlers(self) -> None:
        handler_full_name = (
            f"{ImgExplorationPlugin.on_message.__module__}_"
            f"{ImgExplorationPlugin.on_message.__name__}"
        )
        handler = star_handlers_registry.get_handler_by_full_name(handler_full_name)

        self.assertIsNotNone(handler)
        assert handler is not None
        self.assertEqual(handler.extras_configs["priority"], 1)

    async def test_image_without_wait_remains_unstopped(self) -> None:
        plugin = self.make_wait_plugin()
        plugin._run_command_search = AsyncMock(return_value=None)
        image_event = FakeEvent(
            [],
            message_str="",
            messages=[Image(file="base64://ordinary-image")],
            is_command=False,
        )

        with self.mock_image_context():
            await plugin.on_message(image_event)

        self.assertFalse(image_event.is_stopped())
        plugin._run_command_search.assert_not_awaited()

    async def test_command_without_image_waits_with_strategies(self) -> None:
        clock = Mock(return_value=100.0)
        plugin = self.make_wait_plugin(clock=clock)
        plugin._run_command_search = AsyncMock(return_value=None)
        wait_event = FakeEvent(
            [],
            message_str="搜图 saucenao",
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )
        image = Image(file="base64://matching")
        image_event = FakeEvent(
            [],
            message_str="",
            messages=[image],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
            is_command=False,
        )
        handler = plugin.search_image_cmd(wait_event)

        first_result = await anext(handler)
        wait_completion = asyncio.create_task(anext(handler))
        await asyncio.sleep(0)
        clock.return_value = 110.0
        with self.mock_image_context():
            await plugin.on_message(image_event)

        self.assertEqual(first_result, "请在60秒内发送图片。")
        await self.assert_async_iteration_stops(wait_completion)
        plugin._run_command_search.assert_awaited_once_with(
            image_event,
            image,
            ["saucenao"],
        )
        self.assertTrue(image_event.is_stopped())

    async def test_context_capture_precedes_waited_search_and_stop(self) -> None:
        timeline: list[tuple[str, object]] = []
        clock = Mock(return_value=100.0)
        plugin = self.make_wait_plugin(clock=clock)
        wait_event = FakeEvent(
            timeline,
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )
        image_url = "https://example.com/waited.jpg"
        image = Image(file=image_url)
        image_event = FakeEvent(
            timeline,
            message_str="",
            messages=[image],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
            is_command=False,
        )
        image_context = SimpleNamespace(
            add_image=Mock(
                side_effect=lambda _event, url, **_kwargs: timeline.append(
                    ("context", url)
                )
            )
        )

        async def run_search(*_args: object) -> None:
            timeline.append(("search", image))
            return None

        plugin._run_command_search = AsyncMock(side_effect=run_search)
        handler = plugin.search_image_cmd(wait_event)
        await anext(handler)
        wait_completion = asyncio.create_task(anext(handler))
        await asyncio.sleep(0)
        clock.return_value = 110.0

        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager",
            return_value=image_context,
        ):
            await plugin.on_message(image_event)

        await self.assert_async_iteration_stops(wait_completion)
        self.assertEqual(
            [entry[0] for entry in timeline],
            ["context", "search", "stop"],
        )
        plugin._run_command_search.assert_awaited_once_with(
            image_event,
            image,
            None,
        )

    async def test_listener_waits_for_search_before_returning(self) -> None:
        clock = Mock(return_value=100.0)
        plugin = self.make_wait_plugin(clock=clock)
        search_started = asyncio.Event()
        release_search = asyncio.Event()

        async def run_search(*_args: object) -> None:
            search_started.set()
            await release_search.wait()
            return None

        plugin._run_command_search = AsyncMock(side_effect=run_search)
        wait_event = FakeEvent(
            [],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )
        image_event = FakeEvent(
            [],
            message_str="",
            messages=[Image(file="base64://matching")],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
            is_command=False,
        )
        handler = plugin.search_image_cmd(wait_event)
        await anext(handler)
        wait_completion = asyncio.create_task(anext(handler))
        await asyncio.sleep(0)
        clock.return_value = 110.0

        with self.mock_image_context():
            image_task = asyncio.create_task(plugin.on_message(image_event))
            await asyncio.wait_for(search_started.wait(), timeout=0.5)
            await self.assert_async_iteration_stops(wait_completion)
            self.assertFalse(image_task.done())
            self.assertFalse(image_event.is_stopped())
            release_search.set()
            await image_task

        self.assertTrue(image_event.is_stopped())

    async def test_wait_times_out_without_another_message(self) -> None:
        plugin = self.make_wait_plugin(
            timeout_seconds=0.01,
            clock=Mock(wraps=asyncio.get_running_loop().time),
        )
        plugin._run_command_search = AsyncMock(return_value=None)
        event = FakeEvent([])
        handler = plugin.search_image_cmd(event)

        await anext(handler)
        timeout_result = await asyncio.wait_for(anext(handler), timeout=0.5)

        self.assertEqual(timeout_result, "搜图等待已超时")
        plugin._run_command_search.assert_not_awaited()
        await self.assert_async_iteration_stops(anext(handler))

    async def test_repeated_command_keeps_existing_wait(self) -> None:
        clock = Mock(return_value=100.0)
        plugin = self.make_wait_plugin(clock=clock)
        plugin._run_command_search = AsyncMock(return_value=None)
        first_event = FakeEvent(
            [],
            message_str="搜图 saucenao",
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )
        second_event = FakeEvent(
            [],
            message_str="搜图 ascii2d",
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )
        matching_image = Image(file="base64://matching")
        matching_event = FakeEvent(
            [],
            message_str="",
            messages=[matching_image],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
            is_command=False,
        )

        first_handler = plugin.search_image_cmd(first_event)
        first_result = await anext(first_handler)
        first_completion = asyncio.create_task(anext(first_handler))
        await asyncio.sleep(0)
        clock.return_value = 120.0
        second_handler = plugin.search_image_cmd(second_event)
        second_result = await anext(second_handler)
        with self.mock_image_context():
            await plugin.on_message(matching_event)

        self.assertEqual(first_result, "请在60秒内发送图片。")
        self.assertEqual(second_result, "当前已进入搜索模式，请直接发送图片")
        await self.assert_async_iteration_stops(anext(second_handler))
        await self.assert_async_iteration_stops(first_completion)
        plugin._run_command_search.assert_awaited_once_with(
            matching_event,
            matching_image,
            ["saucenao"],
        )
        self.assertTrue(matching_event.is_stopped())

    async def test_wait_isolated_by_session_and_sender_and_ignores_text(
        self,
    ) -> None:
        clock = Mock(return_value=100.0)
        plugin = self.make_wait_plugin(clock=clock)
        wait_event = FakeEvent(
            [],
            message_str="搜图 saucenao",
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )
        handler = plugin.search_image_cmd(wait_event)
        await anext(handler)
        clock.return_value = 110.0
        plugin._run_command_search = AsyncMock(return_value=None)
        wait_completion = asyncio.create_task(anext(handler))
        await asyncio.sleep(0)

        other_member_event = FakeEvent(
            [],
            message_str="",
            messages=[Image(file="base64://other-member")],
            unified_msg_origin="test:group:100",
            sender_id="user-b",
            is_command=False,
        )
        other_session_event = FakeEvent(
            [],
            message_str="",
            messages=[Image(file="base64://other-session")],
            unified_msg_origin="test:group:200",
            sender_id="user-a",
            is_command=False,
        )
        text_event = FakeEvent(
            [],
            message_str="仍在等待",
            unified_msg_origin="test:group:100",
            sender_id="user-a",
            is_command=False,
        )
        matching_image = Image(file="base64://matching")
        matching_event = FakeEvent(
            [],
            message_str="",
            messages=[matching_image],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
            is_command=False,
        )

        with self.mock_image_context():
            await plugin.on_message(other_member_event)
            await plugin.on_message(other_session_event)
            await plugin.on_message(text_event)
            plugin._run_command_search.assert_not_awaited()
            await plugin.on_message(matching_event)

        self.assertFalse(other_member_event.is_stopped())
        self.assertFalse(other_session_event.is_stopped())
        self.assertFalse(text_event.is_stopped())
        self.assertTrue(matching_event.is_stopped())
        await self.assert_async_iteration_stops(wait_completion)
        plugin._run_command_search.assert_awaited_once_with(
            matching_event,
            matching_image,
            ["saucenao"],
        )

    async def test_late_image_completes_wait_with_one_timeout(self) -> None:
        clock = Mock(return_value=100.0)
        plugin = self.make_wait_plugin(clock=clock)
        wait_event = FakeEvent(
            [],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )
        handler = plugin.search_image_cmd(wait_event)
        await anext(handler)
        clock.return_value = 161.0
        plugin._run_command_search = AsyncMock(return_value=None)
        image_event = FakeEvent(
            [],
            message_str="",
            messages=[Image(file="base64://late")],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
            is_command=False,
        )

        with self.mock_image_context():
            await plugin.on_message(image_event)

        timeout_result = await anext(handler)

        self.assertEqual(timeout_result, "搜图等待已超时")
        plugin._run_command_search.assert_not_awaited()
        self.assertFalse(image_event.is_stopped())
        await self.assert_async_iteration_stops(anext(handler))

    async def test_concurrent_images_stop_and_search_exactly_one(self) -> None:
        clock = Mock(return_value=100.0)
        plugin = self.make_wait_plugin(clock=clock)
        plugin._run_command_search = AsyncMock(return_value=None)
        wait_event = FakeEvent(
            [],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )
        image_events = [
            FakeEvent(
                [],
                message_str="",
                messages=[Image(file=f"base64://image-{index}")],
                unified_msg_origin="test:group:100",
                sender_id="user-a",
                is_command=False,
            )
            for index in range(2)
        ]
        handler = plugin.search_image_cmd(wait_event)
        await anext(handler)
        wait_completion = asyncio.create_task(anext(handler))
        await asyncio.sleep(0)
        clock.return_value = 110.0

        with self.mock_image_context():
            await asyncio.gather(
                *(plugin.on_message(event) for event in image_events),
            )

        await self.assert_async_iteration_stops(wait_completion)
        self.assertEqual(plugin._run_command_search.await_count, 1)
        self.assertEqual(sum(event.is_stopped() for event in image_events), 1)

    async def test_waited_image_sends_terminal_message_through_image_event(
        self,
    ) -> None:
        timeline: list[tuple[str, object]] = []
        clock = Mock(return_value=100.0)
        plugin = self.make_wait_plugin(clock=clock)
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
        clock.return_value = 110.0
        wait_completion = asyncio.create_task(anext(handler))
        await asyncio.sleep(0)

        with self.mock_image_context():
            await plugin.on_message(image_event)

        await self.assert_async_iteration_stops(wait_completion)
        self.assertEqual(
            timeline,
            [("send", "获取图片失败"), ("stop", None)],
        )
        plugin._run_command_search.assert_awaited_once_with(
            image_event,
            image,
            None,
        )


class ImageWaitPluginLifecycleTests(_ImageWaitPluginTestCase):
    async def test_command_image_does_not_consume_wait_twice(self) -> None:
        clock = Mock(return_value=100.0)
        plugin = self.make_wait_plugin(clock=clock)
        wait_event = FakeEvent(
            [],
            message_str="搜图 saucenao",
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )
        wait_handler = plugin.search_image_cmd(wait_event)
        await anext(wait_handler)
        clock.return_value = 110.0
        plugin._run_command_search = AsyncMock(return_value=None)
        wait_completion = asyncio.create_task(anext(wait_handler))
        await asyncio.sleep(0)
        image = Image(file="base64://command-image")
        command_event = FakeEvent(
            [],
            message_str="搜图",
            messages=[image],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
            is_command=True,
        )

        with self.mock_image_context():
            await plugin.on_message(command_event)
        plugin._run_command_search.assert_not_awaited()
        self.assertFalse(command_event.is_stopped())

        yielded = [result async for result in plugin.search_image_cmd(command_event)]

        self.assertEqual(yielded, [])
        plugin._run_command_search.assert_awaited_once_with(
            command_event,
            image,
            None,
        )
        self.assertFalse(command_event.is_stopped())
        await self.assert_async_iteration_stops(wait_completion)

    async def test_closing_wait_handler_clears_current_state(self) -> None:
        plugin = self.make_wait_plugin(clock=Mock(return_value=100.0))
        event = FakeEvent([])
        handler = plugin.search_image_cmd(event)

        await anext(handler)
        await handler.aclose()

        replacement = await plugin._image_wait.create(event, None)
        self.assertIsNotNone(replacement)
        await plugin._image_wait.close()

    async def test_cancelling_active_wait_clears_current_state(self) -> None:
        plugin = self.make_wait_plugin(clock=Mock(return_value=100.0))
        event = FakeEvent([])
        handler = plugin.search_image_cmd(event)

        await anext(handler)
        wait_task = asyncio.create_task(anext(handler))
        await asyncio.sleep(0)
        wait_task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await wait_task
        replacement = await plugin._image_wait.create(event, None)
        self.assertIsNotNone(replacement)
        await plugin._image_wait.close()
        await handler.aclose()


class PluginTerminationTests(_ImageWaitPluginTestCase):
    async def test_terminate_leaves_llm_tool_lifecycle_to_framework(self) -> None:
        plugin = self.make_wait_plugin(clock=Mock(return_value=100.0))
        state = await plugin._image_wait.create(FakeEvent([]), None)
        assert state is not None
        plugin.strategies = []
        get_llm_tool_manager = Mock()
        plugin.context = SimpleNamespace(
            get_llm_tool_manager=get_llm_tool_manager,
        )

        with patch(
            "astrbot_plugin_imgexploration.main.close_aiohttp_session",
            new=AsyncMock(),
        ) as close_session:
            await plugin.terminate()

        self.assertIs(state.future.result(), ImageWaitOutcome.CANCELLED)
        get_llm_tool_manager.assert_not_called()
        close_session.assert_awaited_once_with()
