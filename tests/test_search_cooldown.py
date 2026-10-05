"""按用户搜图冷却测试"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from astrbot.core.message.components import Image
from astrbot_plugin_imgexploration.core.models import ExplorationResult
from astrbot_plugin_imgexploration.core.search_cooldown import SearchCooldown
from astrbot_plugin_imgexploration.main import ImgExplorationPlugin

from .helpers import FakeEvent, PluginTestCase


class SearchCooldownTests(unittest.TestCase):
    def test_disabled_cooldown_never_limits(self) -> None:
        cooldown = SearchCooldown(0, clock=Mock(return_value=0.0))
        event = FakeEvent([])

        self.assertEqual(cooldown.try_acquire(event), 0)
        self.assertEqual(cooldown.try_acquire(event), 0)

    def test_limits_until_cooldown_expires(self) -> None:
        clock = Mock(return_value=0.0)
        cooldown = SearchCooldown(30, clock=clock)
        event = FakeEvent([])

        self.assertEqual(cooldown.try_acquire(event), 0)
        clock.return_value = 10.5
        self.assertEqual(cooldown.get_remaining(event), 20)
        # 冷却中的请求不会刷新计时
        self.assertEqual(cooldown.try_acquire(event), 20)
        clock.return_value = 30.0
        self.assertEqual(cooldown.try_acquire(event), 0)

    def test_scope_is_platform_and_sender_across_sessions(self) -> None:
        cooldown = SearchCooldown(30, clock=Mock(return_value=0.0))
        cooldown.try_acquire(FakeEvent([], unified_msg_origin="test:group:1"))

        for event, expected in (
            (FakeEvent([], unified_msg_origin="test:group:2"), 30),
            (FakeEvent([], unified_msg_origin="test:private:1"), 30),
            (FakeEvent([], sender_id="user-2"), 0),
            (FakeEvent([], platform_name="telegram"), 0),
            (FakeEvent([], is_admin=True), 0),
            (FakeEvent([], sender_id=""), 0),
        ):
            with self.subTest(
                session=event.unified_msg_origin,
                sender=event.get_sender_id(),
                platform=event.get_platform_name(),
                admin=event.is_admin(),
            ):
                self.assertEqual(cooldown.get_remaining(event), expected)

    def test_admin_and_unknown_sender_searches_are_not_recorded(self) -> None:
        cooldown = SearchCooldown(30, clock=Mock(return_value=0.0))

        for event in (FakeEvent([], is_admin=True), FakeEvent([], sender_id="")):
            with self.subTest(admin=event.is_admin(), sender=event.get_sender_id()):
                self.assertEqual(cooldown.try_acquire(event), 0)
                self.assertEqual(cooldown.try_acquire(event), 0)


class SearchCooldownPluginTests(PluginTestCase):
    def make_cooldown_plugin(self) -> ImgExplorationPlugin:
        service = SimpleNamespace(
            get_available_strategies=Mock(return_value=["SauceNAO"]),
            resolve_strategy_names=Mock(return_value=([], [])),
            explore=AsyncMock(return_value=ExplorationResult()),
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        plugin.config = {}
        plugin._search_cooldown = SearchCooldown(60, clock=Mock(return_value=0.0))
        return plugin

    async def test_command_search_starts_cooldown_for_next_command(self) -> None:
        plugin = self.make_cooldown_plugin()
        image = Image(file="https://image.example/source.jpg")

        first = FakeEvent([], messages=[image])
        second = FakeEvent([], messages=[image], unified_msg_origin="test:group:2")
        first_results = [result async for result in plugin.search_image_cmd(first)]
        second_results = [result async for result in plugin.search_image_cmd(second)]

        self.assertEqual(
            first_results, ["未找到相关图片来源，请尝试更换图片或稍后重试。"]
        )
        self.assertEqual(second_results, ["搜图过于频繁，请在 60 秒后再试"])
        self.assertEqual(second.timeline, [])
        plugin.service.explore.assert_awaited_once()

    async def test_command_in_cooldown_does_not_create_wait(self) -> None:
        plugin = self.make_cooldown_plugin()
        plugin._search_cooldown.try_acquire(FakeEvent([]))
        event = FakeEvent([])

        results = [result async for result in plugin.search_image_cmd(event)]

        self.assertEqual(results, ["搜图过于频繁，请在 60 秒后再试"])
        self.assertIsNone(await plugin._image_wait.consume(event))

    async def test_waiting_for_image_does_not_start_cooldown(self) -> None:
        plugin = self.make_cooldown_plugin()
        event = FakeEvent([])
        results = plugin.search_image_cmd(event)

        self.assertEqual(await anext(results), "请在60秒内发送图片。")
        self.assertEqual(plugin._search_cooldown.get_remaining(event), 0)
        await results.aclose()

    async def test_waited_image_is_rejected_if_cooldown_started_meanwhile(
        self,
    ) -> None:
        plugin = self.make_cooldown_plugin()
        command = FakeEvent([])
        results = plugin.search_image_cmd(command)
        self.assertEqual(await anext(results), "请在60秒内发送图片。")

        # 等待期间通过 LLM 工具等其他途径完成了一次搜索
        plugin._search_cooldown.try_acquire(FakeEvent([]))
        timeline: list[tuple[str, object]] = []
        image_event = FakeEvent(
            timeline,
            message_str="",
            messages=[Image(file="https://image.example/source.jpg")],
        )
        await plugin.on_message(image_event)
        await results.aclose()

        self.assertEqual(
            timeline,
            [("send", "搜图过于频繁，请在 60 秒后再试"), ("stop", None)],
        )
        plugin.service.explore.assert_not_awaited()

    async def test_llm_tool_shares_cooldown_with_commands(self) -> None:
        plugin = self.make_cooldown_plugin()
        source_url = "https://image.example/source.jpg"
        event = FakeEvent([])

        with (
            patch(
                "astrbot_plugin_imgexploration.main.get_image_context_manager",
                return_value=MagicMock(
                    get_image_by_index=Mock(return_value=source_url)
                ),
            ),
            patch(
                "astrbot_plugin_imgexploration.main.get_http_image_url",
                new=AsyncMock(return_value=source_url),
            ),
        ):
            first = json.loads(await plugin.tool_search_image(event, image_index=-1))
            second = json.loads(await plugin.tool_search_image(event, image_index=-1))

        self.assertEqual(first["error"], "未找到相关图片来源")
        self.assertEqual(
            second,
            {
                "success": False,
                "error": "搜图过于频繁，请在 60 秒后再试",
                "retry_after_seconds": 60,
            },
        )
        plugin.service.explore.assert_awaited_once()

        command_results = [
            result
            async for result in plugin.search_image_cmd(
                FakeEvent([], messages=[Image(file=source_url)])
            )
        ]
        self.assertEqual(command_results, ["搜图过于频繁，请在 60 秒后再试"])

    def test_cooldown_config_normalization(self) -> None:
        for value, expected in (
            (None, 0),
            ("", 0),
            ("abc", 0),
            (True, 0),
            (-5, 0),
            (0, 0),
            ("30", 30),
            (45, 45),
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    ImgExplorationPlugin._normalize_search_cooldown(value), expected
                )
