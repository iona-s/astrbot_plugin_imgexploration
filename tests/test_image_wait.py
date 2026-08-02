from __future__ import annotations

import asyncio
import json
from unittest.mock import Mock

from astrbot_plugin_imgexploration.core.image_wait import (
    ImageWaitConsumption,
    ImageWaitCoordinator,
    ImageWaitOutcome,
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
        self.assertEqual(normalize(None), 60)

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

        other_member_result = await coordinator.consume(
            FakeEvent(
                [],
                unified_msg_origin="test:group:100",
                sender_id="user-b",
            ),
        )
        other_session_result = await coordinator.consume(
            FakeEvent(
                [],
                unified_msg_origin="test:group:200",
                sender_id="user-a",
            ),
        )
        self.assertIsNone(other_member_result)
        self.assertIsNone(other_session_result)
        self.assertFalse(state.future.done())

        matching_event = FakeEvent(
            [],
            unified_msg_origin="test:group:100",
            sender_id="user-a",
        )
        consumption = await coordinator.consume(matching_event)

        result = state.future.result()
        self.assertIsInstance(consumption, ImageWaitConsumption)
        self.assertIs(result, consumption)
        assert isinstance(consumption, ImageWaitConsumption)
        self.assertEqual(consumption.strategy_names, ("saucenao",))

    async def test_concurrent_images_consume_wait_once(self) -> None:
        coordinator = ImageWaitCoordinator(60, clock=Mock(return_value=100.0))
        wait_event = FakeEvent([])
        state = await coordinator.create(wait_event, None)
        assert state is not None

        consumptions = await asyncio.gather(
            coordinator.consume(wait_event),
            coordinator.consume(wait_event),
        )

        result = state.future.result()
        self.assertIsInstance(result, ImageWaitConsumption)
        self.assertEqual(
            sum(isinstance(value, ImageWaitConsumption) for value in consumptions),
            1,
        )
        self.assertEqual(sum(value is None for value in consumptions), 1)
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

        consumption = await coordinator.consume(event)

        self.assertIsNone(consumption)
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
        consumption = await coordinator.consume(event)

        self.assertIs(old_state.future.result(), ImageWaitOutcome.CANCELLED)
        result = current_state.future.result()
        self.assertIs(result, consumption)
        self.assertIsInstance(consumption, ImageWaitConsumption)
        assert isinstance(consumption, ImageWaitConsumption)
        self.assertEqual(consumption.strategy_names, ("ascii2d",))

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
