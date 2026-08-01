from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from astrbot.core.message.components import Image
from astrbot_plugin_imgexploration.core.image_wait import (
    ImageWaitCoordinator,
    ImageWaitOutcome,
    ImageWaitSubmission,
)
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


class ImageWaitCoordinatorTests(PluginTestCase):
    async def test_create_copies_strategies_and_rejects_existing_wait(self) -> None:
        coordinator = ImageWaitCoordinator(60, clock=Mock(return_value=100.0))
        event = FakeEvent(
            [],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )
        strategy_names = ["saucenao"]

        state = await coordinator.create(event, strategy_names)
        strategy_names.append("ascii2d")
        repeated_state = await coordinator.create(event, ["ascii2d"])

        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.strategy_names, ["saucenao"])
        self.assertEqual(state.expires_at, 160.0)
        self.assertFalse(state.future.done())
        self.assertIsNone(repeated_state)
        await coordinator.close()

    async def test_consume_is_isolated_by_session_and_sender(self) -> None:
        coordinator = ImageWaitCoordinator(60, clock=Mock(return_value=100.0))
        wait_event = FakeEvent(
            [],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )
        state = await coordinator.create(wait_event, ["saucenao"])
        assert state is not None

        await coordinator.consume(
            FakeEvent(
                [],
                unified_msg_origin="test:group:100",
                sender_id="user-b",
            ),
            Image(file="base64://other-member"),
        )
        await coordinator.consume(
            FakeEvent(
                [],
                unified_msg_origin="test:group:200",
                sender_id="user-a",
            ),
            Image(file="base64://other-session"),
        )
        self.assertFalse(state.future.done())

        matching_image = Image(file="base64://matching")
        matching_event = FakeEvent(
            [],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )
        await coordinator.consume(matching_event, matching_image)

        result = state.future.result()
        self.assertIsInstance(result, ImageWaitSubmission)
        assert isinstance(result, ImageWaitSubmission)
        self.assertIs(result.event, matching_event)
        self.assertIs(result.image, matching_image)

    async def test_concurrent_images_consume_wait_once(self) -> None:
        coordinator = ImageWaitCoordinator(60, clock=Mock(return_value=100.0))
        wait_event = FakeEvent([])
        state = await coordinator.create(wait_event, None)
        assert state is not None
        first_image = Image(file="base64://first")
        second_image = Image(file="base64://second")

        await asyncio.gather(
            coordinator.consume(wait_event, first_image),
            coordinator.consume(wait_event, second_image),
        )

        result = state.future.result()
        self.assertIsInstance(result, ImageWaitSubmission)
        assert isinstance(result, ImageWaitSubmission)
        self.assertIn(result.image, (first_image, second_image))
        replacement = await coordinator.create(wait_event, None)
        self.assertIsNotNone(replacement)
        await coordinator.close()

    async def test_late_image_resolves_wait_as_timed_out(self) -> None:
        clock = Mock(return_value=100.0)
        coordinator = ImageWaitCoordinator(60, clock=clock)
        event = FakeEvent([])
        state = await coordinator.create(event, None)
        assert state is not None
        clock.return_value = 161.0

        await coordinator.consume(event, Image(file="base64://late"))

        self.assertIs(state.future.result(), ImageWaitOutcome.TIMED_OUT)

    async def test_wait_times_out_without_another_event(self) -> None:
        coordinator = ImageWaitCoordinator(
            0.01,
            clock=asyncio.get_running_loop().time,
        )
        event = FakeEvent([])
        state = await coordinator.create(event, None)
        assert state is not None

        result = await asyncio.wait_for(
            coordinator.wait(event, state),
            timeout=0.5,
        )

        self.assertIs(result, ImageWaitOutcome.TIMED_OUT)
        replacement = await coordinator.create(event, None)
        self.assertIsNotNone(replacement)
        await coordinator.close()

    async def test_clear_only_removes_the_expected_state(self) -> None:
        coordinator = ImageWaitCoordinator(60, clock=Mock(return_value=100.0))
        event = FakeEvent([])
        old_state = await coordinator.create(event, None)
        assert old_state is not None
        await coordinator.clear(event, expected_state=old_state)
        current_state = await coordinator.create(event, ["ascii2d"])
        assert current_state is not None

        await coordinator.clear(event, expected_state=old_state)
        image = Image(file="base64://current")
        await coordinator.consume(event, image)

        self.assertIs(old_state.future.result(), ImageWaitOutcome.CANCELLED)
        result = current_state.future.result()
        self.assertIsInstance(result, ImageWaitSubmission)
        assert isinstance(result, ImageWaitSubmission)
        self.assertIs(result.image, image)

    async def test_create_cleans_other_expired_entries(self) -> None:
        coordinator = ImageWaitCoordinator(
            60,
            clock=Mock(side_effect=[100.0, 200.0, 200.0]),
        )
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

        expired_state = await coordinator.create(expired_event, None)
        current_state = await coordinator.create(current_event, ["ascii2d"])
        assert expired_state is not None
        assert current_state is not None

        self.assertIs(expired_state.future.result(), ImageWaitOutcome.TIMED_OUT)
        self.assertFalse(current_state.future.done())
        await coordinator.clear(current_event)
        self.assertIs(current_state.future.result(), ImageWaitOutcome.CANCELLED)

    async def test_cancelling_wait_clears_state_and_reraises(self) -> None:
        coordinator = ImageWaitCoordinator(60, clock=Mock(return_value=100.0))
        event = FakeEvent([])
        state = await coordinator.create(event, None)
        assert state is not None
        wait_task = asyncio.create_task(coordinator.wait(event, state))
        await asyncio.sleep(0)

        wait_task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await wait_task
        self.assertIs(state.future.result(), ImageWaitOutcome.CANCELLED)
        replacement = await coordinator.create(event, None)
        self.assertIsNotNone(replacement)
        await coordinator.close()

    async def test_close_cancels_all_waits(self) -> None:
        coordinator = ImageWaitCoordinator(60, clock=Mock(return_value=100.0))
        first_state = await coordinator.create(
            FakeEvent([], unified_msg_origin="test:group:100"),
            None,
        )
        second_state = await coordinator.create(
            FakeEvent([], unified_msg_origin="test:group:200"),
            ["ascii2d"],
        )
        assert first_state is not None
        assert second_state is not None

        await coordinator.close()

        self.assertIs(first_state.future.result(), ImageWaitOutcome.CANCELLED)
        self.assertIs(second_state.future.result(), ImageWaitOutcome.CANCELLED)
        await coordinator.close()


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

    async def assert_async_iteration_stops(
        self,
        awaitable: Awaitable[object],
    ) -> None:
        with self.assertRaises(StopAsyncIteration):
            await awaitable


class ImageWaitFlowTests(_ImageWaitPluginTestCase):
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
        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager",
            return_value=SimpleNamespace(add_image=Mock()),
        ):
            await plugin.on_message(image_event)

        self.assertEqual(first_result, "请在60秒内发送图片。")
        await self.assert_async_iteration_stops(wait_completion)
        plugin._run_command_search.assert_awaited_once_with(
            image_event,
            image,
            ["saucenao"],
        )

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
        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager",
            return_value=SimpleNamespace(add_image=Mock()),
        ):
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

        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager",
            return_value=SimpleNamespace(add_image=Mock()),
        ):
            await plugin.on_message(other_member_event)
            await plugin.on_message(other_session_event)
            await plugin.on_message(text_event)
            plugin._run_command_search.assert_not_awaited()
            await plugin.on_message(matching_event)

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

        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager",
            return_value=SimpleNamespace(add_image=Mock()),
        ):
            await plugin.on_message(image_event)

        timeout_result = await anext(handler)

        self.assertEqual(timeout_result, "搜图等待已超时")
        plugin._run_command_search.assert_not_awaited()
        await self.assert_async_iteration_stops(anext(handler))

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

        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager",
            return_value=SimpleNamespace(add_image=Mock()),
        ):
            await plugin.on_message(command_event)
        plugin._run_command_search.assert_not_awaited()

        yielded = [result async for result in plugin.search_image_cmd(command_event)]

        self.assertEqual(yielded, [])
        plugin._run_command_search.assert_awaited_once_with(
            command_event,
            image,
            None,
        )
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
