from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from astrbot_plugin_imgexploration.core.image_context import ImageContextManager
from astrbot_plugin_imgexploration.core.models import ExplorationResult
from astrbot_plugin_imgexploration.core.service import ImgExplorationService

from .helpers import FakeEvent, PluginTestCase


class LoggingPolicyTests(PluginTestCase):
    image_url = (
        "https://image.example/source.jpg?"
        "fileid=long-signed-image-identifier&rkey=private-access-parameter"
    )

    def test_image_capture_debug_log_matches_readme_and_keeps_full_url(
        self,
    ) -> None:
        manager = ImageContextManager()
        event = SimpleNamespace(session_id="session-1")

        with patch(
            "astrbot_plugin_imgexploration.core.image_context.logger.debug"
        ) as log_debug:
            manager.add_image(event, self.image_url)

        message = str(log_debug.call_args.args[0])
        self.assertIn("捕获图片到上下文", message)
        self.assertIn("image_id=", message)
        self.assertIn(self.image_url, message)

    async def test_llm_search_logs_url_only_at_debug(self) -> None:
        service = SimpleNamespace(
            get_available_strategies=Mock(return_value=["saucenao"]),
            explore=AsyncMock(return_value=ExplorationResult()),
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        image_context = SimpleNamespace(
            get_image_by_id=Mock(return_value=self.image_url),
            get_image_by_index=Mock(),
        )
        event = FakeEvent([])

        with (
            patch(
                "astrbot_plugin_imgexploration.main.get_image_context_manager",
                return_value=image_context,
            ),
            patch(
                "astrbot_plugin_imgexploration.main.get_http_image_url",
                new=AsyncMock(return_value=self.image_url),
            ),
            patch("astrbot_plugin_imgexploration.main.logger.info") as log_info,
            patch("astrbot_plugin_imgexploration.main.logger.debug") as log_debug,
        ):
            await plugin.tool_search_image(event, image_id="image-1")

        info_messages = " ".join(str(call.args[0]) for call in log_info.call_args_list)
        debug_messages = " ".join(
            str(call.args[0]) for call in log_debug.call_args_list
        )
        self.assertIn("AI 工具调用搜图", info_messages)
        self.assertNotIn(self.image_url, info_messages)
        self.assertNotIn(self.image_url[:50], info_messages)
        self.assertIn(self.image_url, debug_messages)
        service.explore.assert_awaited_once_with(
            self.image_url,
            strategy_names=None,
        )

    async def test_service_logs_url_only_at_debug(self) -> None:
        strategy = SimpleNamespace(
            get_service_name=Mock(return_value="SauceNAO"),
            search=AsyncMock(return_value=[]),
        )
        service = ImgExplorationService([strategy])

        with (
            patch("astrbot_plugin_imgexploration.core.service.logger.info") as log_info,
            patch(
                "astrbot_plugin_imgexploration.core.service.logger.debug"
            ) as log_debug,
        ):
            await service.explore(self.image_url)

        info_messages = " ".join(str(call.args[0]) for call in log_info.call_args_list)
        debug_messages = " ".join(
            str(call.args[0]) for call in log_debug.call_args_list
        )
        self.assertIn("开始搜图", info_messages)
        self.assertIn("SauceNAO", info_messages)
        self.assertNotIn(self.image_url, info_messages)
        self.assertNotIn(self.image_url[:50], info_messages)
        self.assertIn(self.image_url, debug_messages)
        strategy.search.assert_awaited_once_with(self.image_url)
