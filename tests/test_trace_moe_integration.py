from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock, patch

from astrbot.core.message.components import Image
from astrbot_plugin_imgexploration.core import image_context, utils
from astrbot_plugin_imgexploration.core.image_context import ImageContextManager
from astrbot_plugin_imgexploration.core.models import (
    ProviderSearchOutcome,
    SearchResultItem,
)
from astrbot_plugin_imgexploration.main import ImgExplorationPlugin

from tests.helpers import FakeEvent, PluginTestCase


class TraceMoeIntegrationTests(PluginTestCase):
    def setUp(self):
        super().setUp()
        # 真实插件初始化会修改模块级全局状态，测试结束后恢复原值
        for module, name in (
            (utils, "_proxy_url"),
            (utils, "_allow_image_upload"),
            (utils, "_allow_local_file_access"),
            (image_context, "_image_context_manager"),
        ):
            patcher = patch.object(module, name, getattr(module, name))
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_configured_plugin(
        self, enabled=False, include=False, *, sauce_enabled=False
    ):
        return ImgExplorationPlugin(
            Mock(),
            {
                "saucenao": {"enable": sauce_enabled, "api_key": "test-saucenao-key"},
                "google_lens": {"enable": False},
                "ascii2d": {"enable": False},
                "trace_moe": {"enable": enabled, "include_in_default": include},
                "config_version": 2,
            },
        )

    async def test_enable_and_default_switches(self):
        for enabled, include in (
            (False, False),
            (False, True),
            (True, False),
            (True, True),
        ):
            with self.subTest(enabled=enabled, include=include):
                plugin = self.make_configured_plugin(enabled, include)
                self.assertEqual(
                    plugin.service.get_available_strategies(),
                    ["trace.moe"] if enabled else [],
                )
                defaults, _ = plugin.service.resolve_strategy_names(None)
                self.assertEqual(len(defaults), int(enabled and include))
                for alias in ("anime", "trace", "tracemoe", "whatanime"):
                    resolved, missing = plugin.service.resolve_strategy_names([alias])
                    self.assertEqual(len(resolved), int(enabled))
                    self.assertEqual(missing, [] if enabled else [alias])
                await plugin.terminate()

    async def test_disabled_strategy_rejects_command(self):
        plugin = self.make_configured_plugin()
        event = FakeEvent(
            [],
            message_str="搜图 anime",
            messages=[Image(file="https://example.com/image")],
        )
        self.assertEqual(len([r async for r in plugin.search_image_cmd(event)]), 1)
        self.assertFalse(event.timeline)
        await plugin.terminate()

    async def test_explicit_only_rejects_no_argument_without_wait(self):
        plugin = self.make_configured_plugin(True)
        with (
            patch.object(plugin._image_wait, "create", new=AsyncMock()) as wait,
            patch.object(plugin.service, "explore", new=AsyncMock()) as explore,
        ):
            messages = [r async for r in plugin.search_image_cmd(FakeEvent([]))]
        self.assertEqual(len(messages), 1)
        wait.assert_not_awaited()
        explore.assert_not_awaited()
        await plugin.terminate()

    async def test_explicit_command_and_default_search(self):
        for include in (False, True):
            plugin = self.make_configured_plugin(True, include)
            provider = plugin.strategies[0]
            outcome = ProviderSearchOutcome(
                items=[
                    SearchResultItem(
                        "番名",
                        "",
                        source="trace.moe",
                        description="第2集\n文件内时间点: 01:23",
                    )
                ]
            )
            event = FakeEvent(
                [],
                message_str="搜图" if include else "搜图 anime",
                messages=[Image(file="https://example.com/image")],
            )
            with (
                patch.object(
                    provider, "search", new=AsyncMock(return_value=outcome)
                ) as search,
                patch(
                    "astrbot_plugin_imgexploration.main.result_sender.send_search_results",
                    new=AsyncMock(),
                ) as sender,
            ):
                self.assertEqual([r async for r in plugin.search_image_cmd(event)], [])
                search.assert_awaited_once_with("https://example.com/image")
                self.assertEqual(
                    sender.await_args.args[1][0].description,
                    outcome.items[0].description,
                )
            await plugin.terminate()

    async def test_llm_preserves_details_and_actual_used_strategy(self):
        plugin = self.make_configured_plugin(True, sauce_enabled=True)
        providers = {
            strategy.get_service_name(): strategy for strategy in plugin.strategies
        }
        self.assertEqual(set(providers), {"SauceNAO", "trace.moe"})
        event = FakeEvent([], message_str="这是什么番", is_command=False)
        manager = ImageContextManager()
        manager.add_image(event, "https://example.com/image")
        description = "第2集\n文件内时间点: 01:23"
        item = SearchResultItem("番名", "", source="trace.moe", description=description)
        with (
            patch(
                "astrbot_plugin_imgexploration.main.get_image_context_manager",
                return_value=manager,
            ),
            patch.object(
                providers["trace.moe"],
                "search",
                new=AsyncMock(return_value=ProviderSearchOutcome(items=[item])),
            ) as anime_search,
            patch.object(
                providers["SauceNAO"], "search", new=AsyncMock()
            ) as sauce_search,
            patch(
                "astrbot_plugin_imgexploration.main.result_sender.send_search_results",
                new=AsyncMock(),
            ),
        ):
            result = json.loads(
                await plugin.tool_search_image(
                    event, image_index=-1, strategies="anime"
                )
            )
        self.assertEqual(result["items"][0]["description"], description)
        self.assertEqual(result["used_strategies"], ["trace.moe"])
        self.assertEqual(set(result["available_strategies"]), {"SauceNAO", "trace.moe"})
        anime_search.assert_awaited_once_with("https://example.com/image")
        sauce_search.assert_not_awaited()
        await plugin.terminate()

    async def test_anime_wait_keeps_selection_and_searches_once(self):
        plugin = self.make_configured_plugin(True)
        command = plugin.search_image_cmd(FakeEvent([], message_str="搜图 anime"))
        await anext(command)
        image_event = FakeEvent(
            [],
            message_str="",
            is_command=False,
            messages=[Image(file="https://example.com/image")],
        )
        with patch.object(
            plugin.strategies[0],
            "search",
            new=AsyncMock(return_value=ProviderSearchOutcome()),
        ) as search:
            await plugin.on_message(image_event)
            self.assertEqual([r async for r in command], [])
            search.assert_awaited_once_with("https://example.com/image")
        self.assertTrue(image_event.is_stopped())
        await plugin.terminate()

    async def test_llm_without_default_strategies_rejects_before_image(self):
        plugin = self.make_configured_plugin(True)
        event = FakeEvent([], message_str="查找图片出处", is_command=False)
        manager = ImageContextManager()
        manager.add_image(event, "https://example.com/image")
        with (
            patch(
                "astrbot_plugin_imgexploration.main.get_image_context_manager",
                return_value=manager,
            ),
            patch.object(
                manager, "get_image_by_index", wraps=manager.get_image_by_index
            ) as select_image,
            patch(
                "astrbot_plugin_imgexploration.main.get_http_image_url",
                new=AsyncMock(),
            ) as convert,
            patch.object(plugin.service, "explore", new=AsyncMock()) as explore,
        ):
            result = json.loads(await plugin.tool_search_image(event, image_index=-1))
            self.assertFalse(result["success"])
            select_image.assert_not_called()
            convert.assert_not_awaited()
            explore.assert_not_awaited()
        await plugin.terminate()

    async def test_disabled_llm_search_does_not_contact_provider(self):
        plugin = self.make_configured_plugin(sauce_enabled=True)
        self.assertEqual(plugin.service.get_available_strategies(), ["SauceNAO"])
        event = FakeEvent([], message_str="查找截图出处", is_command=False)
        manager = ImageContextManager()
        manager.add_image(event, "https://example.com/image")
        with (
            patch(
                "astrbot_plugin_imgexploration.main.get_image_context_manager",
                return_value=manager,
            ),
            patch.object(
                manager, "get_image_by_index", wraps=manager.get_image_by_index
            ) as select_image,
            patch.object(plugin.service, "explore", new=AsyncMock()) as explore,
            patch.object(
                plugin.strategies[0], "search", new=AsyncMock()
            ) as sauce_search,
            patch(
                "astrbot_plugin_imgexploration.core.providers.trace_moe_strategy.get_aiohttp_session"
            ) as session,
        ):
            result = json.loads(
                await plugin.tool_search_image(
                    event, image_index=-1, strategies="anime"
                )
            )
            self.assertFalse(result["success"])
            self.assertEqual(result["available_strategies"], ["SauceNAO"])
            select_image.assert_not_called()
            explore.assert_not_awaited()
            sauce_search.assert_not_awaited()
            session.assert_not_called()
        await plugin.terminate()
