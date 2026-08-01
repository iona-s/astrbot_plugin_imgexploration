from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from astrbot_plugin_imgexploration.core.models import (
    ExplorationResult,
    SearchResultItem,
)

from tests.helpers import FakeEvent, PluginTestCase


class LLMToolsTests(PluginTestCase):
    def test_is_llm_tool_silent_mode(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        plugin.config = {"ai_behavior": {"llm_tool_silent_mode": True}}
        self.assertTrue(plugin._is_llm_tool_silent_mode())

        plugin.config = {"ai_behavior": {"llm_tool_silent_mode": False}}
        self.assertFalse(plugin._is_llm_tool_silent_mode())

    async def test_tool_get_session_images(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        event = FakeEvent([])

        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager"
        ) as mock_mgr_fn:
            mock_mgr = MagicMock()
            mock_mgr.get_image_context_info.return_value = {
                "has_images": True,
                "count": 1,
                "images": [{"image_id": "img123", "index": 1}],
                "hint": "1 image available",
            }
            mock_mgr_fn.return_value = mock_mgr

            res_json = await plugin.tool_get_session_images(event)
            res_dict = json.loads(res_json)

            self.assertTrue(res_dict["has_images"])
            self.assertEqual(res_dict["count"], 1)
            mock_mgr.get_image_context_info.assert_called_once_with(event)

    async def test_tool_get_session_images_empty(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        event = FakeEvent([])

        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager"
        ) as mock_mgr_fn:
            mock_mgr = MagicMock()
            mock_mgr.get_image_context_info.return_value = {
                "has_images": False,
                "count": 0,
                "images": [],
                "hint": "no images",
            }
            mock_mgr_fn.return_value = mock_mgr

            res_dict = json.loads(await plugin.tool_get_session_images(event))

            self.assertFalse(res_dict["has_images"])
            self.assertEqual(res_dict["count"], 0)
            self.assertEqual(res_dict["images"], [])
            mock_mgr.get_image_context_info.assert_called_once_with(event)

    async def test_tool_search_image_no_strategies_available(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        plugin.strategies = []
        event = FakeEvent([])

        res_json = await plugin.tool_search_image(event)
        res_dict = json.loads(res_json)

        self.assertFalse(res_dict["success"])
        self.assertIn("没有可用的搜图 API", res_dict["error"])

    async def test_tool_search_image_image_not_found(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        plugin.strategies = [object()]
        event = FakeEvent([])

        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager"
        ) as mock_mgr_fn:
            mock_mgr = MagicMock()
            mock_mgr.get_image_by_id.return_value = None
            mock_mgr.get_image_by_index.return_value = None
            mock_mgr.get_image_context_info.return_value = {
                "has_images": False,
                "count": 0,
            }
            mock_mgr_fn.return_value = mock_mgr

            res_json = await plugin.tool_search_image(event, image_id="non_existent_id")
            res_dict = json.loads(res_json)

            self.assertFalse(res_dict["success"])
            self.assertIn("未找到指定的图片", res_dict["error"])

    async def test_tool_search_image_http_url_conversion_failure(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        plugin.strategies = [object()]
        event = FakeEvent([])

        with (
            patch(
                "astrbot_plugin_imgexploration.main.get_image_context_manager"
            ) as mock_mgr_fn,
            patch(
                "astrbot_plugin_imgexploration.main.get_http_image_url",
                return_value=None,
            ),
        ):
            mock_mgr = MagicMock()
            mock_mgr.get_image_by_index.return_value = "base64://invalid"
            mock_mgr_fn.return_value = mock_mgr

            res_json = await plugin.tool_search_image(event, image_index=-1)
            res_dict = json.loads(res_json)

            self.assertFalse(res_dict["success"])
            self.assertIn("无法获取有效的图片 URL", res_dict["error"])

    async def test_tool_search_image_invalid_strategy_specified(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        plugin.strategies = [object()]
        event = FakeEvent([])

        plugin.service = MagicMock()
        plugin.service.get_available_strategies.return_value = ["SauceNAO"]
        plugin.service.resolve_strategy_names.return_value = ([], ["unknown_strat"])

        with (
            patch(
                "astrbot_plugin_imgexploration.main.get_image_context_manager"
            ) as mock_mgr_fn,
            patch(
                "astrbot_plugin_imgexploration.main.get_http_image_url",
                return_value="https://example.com/target.jpg",
            ),
        ):
            mock_mgr = MagicMock()
            mock_mgr.get_image_by_index.return_value = "https://example.com/target.jpg"
            mock_mgr_fn.return_value = mock_mgr

            res_json = await plugin.tool_search_image(event, strategies="unknown_strat")
            res_dict = json.loads(res_json)

            self.assertFalse(res_dict["success"])
            self.assertIn("以下策略不可用: unknown_strat", res_dict["error"])

    async def test_tool_search_image_by_id_sends_results(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        plugin.strategies = [object()]
        plugin.config = {"ai_behavior": {"llm_tool_silent_mode": False}}
        event = FakeEvent([])
        item = SearchResultItem(
            title="Result Title",
            url="https://source.com/1",
            source="SauceNAO",
            similarity="90%",
        )
        plugin.service = MagicMock()
        plugin.service.get_available_strategies.return_value = ["SauceNAO"]
        plugin.service.resolve_strategy_names.return_value = ([object()], [])
        plugin.service.explore = AsyncMock(return_value=ExplorationResult(items=[item]))
        source_url = "https://example.com/source.jpg"
        http_url = "https://example.com/searchable.jpg"

        with (
            patch(
                "astrbot_plugin_imgexploration.main.get_image_context_manager"
            ) as mock_mgr_fn,
            patch(
                "astrbot_plugin_imgexploration.main.get_http_image_url",
                new=AsyncMock(return_value=http_url),
            ) as mock_convert,
            patch(
                "astrbot_plugin_imgexploration.main.result_sender.send_search_results",
                new=AsyncMock(),
            ) as mock_send,
        ):
            mock_mgr = MagicMock()
            mock_mgr.get_image_by_id.return_value = source_url
            mock_mgr_fn.return_value = mock_mgr

            res_dict = json.loads(
                await plugin.tool_search_image(
                    event,
                    image_id=" img123 ",
                    strategies=" sauce ",
                )
            )

            self.assertTrue(res_dict["success"])
            self.assertTrue(res_dict["message_sent"])
            self.assertEqual(res_dict["selected_by"], "image_id")
            mock_mgr.get_image_by_id.assert_called_once_with(event, "img123")
            mock_mgr.get_image_by_index.assert_not_called()
            mock_convert.assert_awaited_once_with(source_url)
            plugin.service.resolve_strategy_names.assert_called_once_with(["sauce"])
            plugin.service.explore.assert_awaited_once_with(
                http_url,
                strategy_names=["sauce"],
            )
            mock_send.assert_awaited_once_with(event, [item])

    async def test_tool_search_image_by_index_respects_silent_mode(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        plugin.strategies = [object()]
        plugin.config = {"ai_behavior": {"llm_tool_silent_mode": True}}
        event = FakeEvent([])
        item = SearchResultItem(
            title="Result Title",
            url="https://source.com/1",
            source="SauceNAO",
        )
        plugin.service = MagicMock()
        plugin.service.get_available_strategies.return_value = ["SauceNAO"]
        plugin.service.explore = AsyncMock(return_value=ExplorationResult(items=[item]))
        source_url = "https://example.com/source.jpg"
        http_url = "https://example.com/searchable.jpg"

        with (
            patch(
                "astrbot_plugin_imgexploration.main.get_image_context_manager"
            ) as mock_mgr_fn,
            patch(
                "astrbot_plugin_imgexploration.main.get_http_image_url",
                new=AsyncMock(return_value=http_url),
            ) as mock_convert,
            patch(
                "astrbot_plugin_imgexploration.main.result_sender.send_search_results",
                new=AsyncMock(),
            ) as mock_send,
        ):
            mock_mgr = MagicMock()
            mock_mgr.get_image_by_index.return_value = source_url
            mock_mgr_fn.return_value = mock_mgr

            res_dict = json.loads(await plugin.tool_search_image(event, image_index=2))

            self.assertTrue(res_dict["success"])
            self.assertFalse(res_dict["message_sent"])
            self.assertEqual(res_dict["selected_by"], "image_index")
            mock_mgr.get_image_by_id.assert_not_called()
            mock_mgr.get_image_by_index.assert_called_once_with(event, 2)
            mock_convert.assert_awaited_once_with(source_url)
            plugin.service.resolve_strategy_names.assert_not_called()
            plugin.service.explore.assert_awaited_once_with(
                http_url,
                strategy_names=None,
            )
            mock_send.assert_not_awaited()

    async def test_tool_search_image_reports_empty_search_result(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        plugin.strategies = [object()]
        event = FakeEvent([])
        plugin.service = MagicMock()
        plugin.service.get_available_strategies.return_value = ["SauceNAO"]
        plugin.service.explore = AsyncMock(return_value=ExplorationResult())
        source_url = "https://example.com/source.jpg"

        with (
            patch(
                "astrbot_plugin_imgexploration.main.get_image_context_manager"
            ) as mock_mgr_fn,
            patch(
                "astrbot_plugin_imgexploration.main.get_http_image_url",
                new=AsyncMock(return_value=source_url),
            ) as mock_convert,
            patch(
                "astrbot_plugin_imgexploration.main.result_sender.send_search_results",
                new=AsyncMock(),
            ) as mock_send,
        ):
            mock_mgr = MagicMock()
            mock_mgr.get_image_by_index.return_value = source_url
            mock_mgr_fn.return_value = mock_mgr

            res_dict = json.loads(await plugin.tool_search_image(event))

            self.assertFalse(res_dict["success"])
            self.assertIn("未找到相关图片来源", res_dict["error"])
            mock_mgr.get_image_by_index.assert_called_once_with(event, -1)
            mock_convert.assert_awaited_once_with(source_url)
            plugin.service.explore.assert_awaited_once_with(
                source_url,
                strategy_names=None,
            )
            mock_send.assert_not_awaited()
