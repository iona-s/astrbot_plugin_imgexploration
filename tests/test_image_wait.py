from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from astrbot.core.message.components import Image

from astrbot_plugin_imgexploration.main import ImgExplorationPlugin

from . import PLUGIN_ROOT
from .helpers import FakeEvent, PluginTestCase


class ImageWaitConfigurationTests(PluginTestCase):
    def test_normalizes_timeout_to_supported_range(self) -> None:
        normalize = ImgExplorationPlugin._normalize_image_wait_timeout

        self.assertEqual(normalize(60), 60)
        self.assertEqual(normalize(29), 30)
        self.assertEqual(normalize(121), 120)
        self.assertEqual(normalize("90"), 90)
        self.assertEqual(normalize("invalid"), 60)
        self.assertEqual(normalize(True), 60)

    def test_schema_defaults_and_range(self) -> None:
        schema = json.loads(
            (PLUGIN_ROOT / "_conf_schema.json").read_text(encoding="utf-8")
        )
        setting = schema["command"]["items"]["image_wait_timeout_seconds"]

        self.assertEqual(setting["default"], 60)
        self.assertEqual(setting["slider"]["min"], 30)
        self.assertEqual(setting["slider"]["max"], 120)


class _ImageWaitTestCase(PluginTestCase):
    def make_wait_plugin(self) -> ImgExplorationPlugin:
        service = SimpleNamespace(
            get_available_strategies=lambda: ["saucenao", "ascii2d"],
            resolve_strategy_names=lambda _names: ([], []),
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        return plugin

    async def assert_async_iteration_stops(
        self,
        awaitable: Awaitable[object],
    ) -> None:
        with self.assertRaises(StopAsyncIteration):
            await awaitable


class ImageWaitFlowTests(_ImageWaitTestCase):
    async def test_command_without_image_enters_wait_with_strategies(self) -> None:
        plugin = self.make_wait_plugin()
        plugin._image_wait_clock = Mock(return_value=100.0)
        event = FakeEvent(
            [],
            message_str="搜图 saucenao",
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )

        handler = plugin.search_image_cmd(event)
        first_result = await anext(handler)

        self.assertEqual(first_result, "请在60秒内发送图片。")
        state = plugin._image_wait_states[("test:group:100", "user-a")]
        self.assertEqual(state.strategy_names, ["saucenao"])
        self.assertEqual(state.expires_at, 160.0)
        self.assertFalse(state.future.done())

        await plugin._clear_image_wait(event)
        await self.assert_async_iteration_stops(anext(handler))

    async def test_wait_times_out_without_another_message(self) -> None:
        plugin = self.make_wait_plugin()
        plugin._image_wait_timeout_seconds = 0.01
        plugin._image_wait_clock = asyncio.get_running_loop().time
        plugin._run_command_search = AsyncMock(return_value=None)
        event = FakeEvent([])
        handler = plugin.search_image_cmd(event)

        await anext(handler)
        timeout_result = await asyncio.wait_for(anext(handler), timeout=0.5)

        self.assertEqual(timeout_result, "搜图等待已超时")
        self.assertEqual(plugin._image_wait_states, {})
        plugin._run_command_search.assert_not_awaited()
        await self.assert_async_iteration_stops(anext(handler))

    async def test_repeated_command_keeps_existing_wait(self) -> None:
        plugin = self.make_wait_plugin()
        plugin._image_wait_clock = Mock(return_value=100.0)
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

        first_handler = plugin.search_image_cmd(first_event)
        first_result = await anext(first_handler)
        first_state = plugin._image_wait_states[("test:group:100", "user-a")]

        plugin._image_wait_clock = Mock(return_value=120.0)
        second_handler = plugin.search_image_cmd(second_event)
        second_result = await anext(second_handler)

        self.assertEqual(first_result, "请在60秒内发送图片。")
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
        plugin = self.make_wait_plugin()
        plugin._image_wait_clock = Mock(return_value=100.0)
        wait_event = FakeEvent(
            [],
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

        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager",
            return_value=SimpleNamespace(add_image=Mock()),
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

    async def test_late_image_completes_wait_with_one_timeout(self) -> None:
        plugin = self.make_wait_plugin()
        plugin._image_wait_clock = Mock(return_value=100.0)
        wait_event = FakeEvent(
            [],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )
        handler = plugin.search_image_cmd(wait_event)
        await anext(handler)
        plugin._image_wait_clock = Mock(return_value=161.0)
        plugin._run_command_search = AsyncMock(return_value=None)
        image_event = FakeEvent(
            [],
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
        plugin._run_command_search.assert_not_awaited()
        self.assertEqual(plugin._image_wait_states, {})
        await self.assert_async_iteration_stops(anext(handler))

    async def test_waited_image_sends_terminal_message_through_image_event(
        self,
    ) -> None:
        timeline: list[tuple[str, object]] = []
        plugin = self.make_wait_plugin()
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


class ImageWaitConcurrencyTests(_ImageWaitTestCase):
    async def test_concurrent_images_consume_wait_once(self) -> None:
        plugin = self.make_wait_plugin()
        plugin._image_wait_clock = Mock(return_value=100.0)
        wait_event = FakeEvent(
            [],
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
            [],
            message_str="",
            messages=[Image(file="base64://first")],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
            is_command=False,
        )
        second_event = FakeEvent(
            [],
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
        plugin = self.make_wait_plugin()
        plugin._image_wait_clock = Mock(return_value=100.0)
        wait_event = FakeEvent(
            [],
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
            [],
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

    async def test_closing_wait_handler_clears_current_state(self) -> None:
        plugin = self.make_wait_plugin()
        plugin._image_wait_clock = Mock(return_value=100.0)
        event = FakeEvent([])
        handler = plugin.search_image_cmd(event)

        await anext(handler)
        state = plugin._image_wait_states[("test:group:1", "user-1")]
        await handler.aclose()

        self.assertEqual(plugin._image_wait_states, {})
        self.assertTrue(state.future.done())
        self.assertEqual(state.future.result().value, "cancelled")

    async def test_cancelling_active_wait_clears_current_state(self) -> None:
        plugin = self.make_wait_plugin()
        plugin._image_wait_clock = Mock(return_value=100.0)
        event = FakeEvent([])
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

    async def test_setting_wait_cleans_expired_entries(self) -> None:
        plugin = self.make_wait_plugin()
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


class PluginTerminationTests(_ImageWaitTestCase):
    async def test_terminate_leaves_llm_tool_lifecycle_to_framework(self) -> None:
        plugin = self.make_wait_plugin()
        plugin._image_wait_clock = Mock(return_value=100.0)
        state = await plugin._set_image_wait(FakeEvent([]), None)
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

        self.assertEqual(plugin._image_wait_states, {})
        self.assertTrue(state.future.done())
        self.assertEqual(state.future.result().value, "cancelled")
        get_llm_tool_manager.assert_not_called()
        close_session.assert_awaited_once_with()
