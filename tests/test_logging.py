from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from astrbot_plugin_imgexploration.core import utils
from astrbot_plugin_imgexploration.core.image_context import ImageContextManager
from astrbot_plugin_imgexploration.core.models import (
    ExplorationResult,
    ProviderSearchError,
    SearchResultItem,
)
from astrbot_plugin_imgexploration.core.providers.google_lens_strategy import (
    GoogleLensStrategy,
)
from astrbot_plugin_imgexploration.core.providers.sauce_nao_strategy import (
    SauceNaoStrategy,
)
from astrbot_plugin_imgexploration.core.service import ImgExplorationService

from .helpers import FakeEvent, PluginTestCase


def _logged_text(*loggers: Mock) -> str:
    return " ".join(
        str(arg)
        for logger in loggers
        for call in logger.mock_calls
        for arg in call.args
    )


class _UrlLeakingSession:
    """Raise errors whose text embeds the request URL, like aiohttp may do."""

    def get(self, url: str, **kwargs):
        params = kwargs.get("params")
        if params:
            url = f"{url}?api_key={params['api_key']}"
        raise RuntimeError(f"request failed: {url}")


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
        item = SearchResultItem(
            title="Result",
            url="https://result.example/page",
        )
        service = SimpleNamespace(
            get_available_strategies=Mock(return_value=["saucenao"]),
            explore=AsyncMock(return_value=ExplorationResult(items=[item])),
        )
        plugin = self.make_plugin(service)
        plugin.strategies = [object()]
        plugin.config = {"ai_behavior": {"llm_tool_silent_mode": False}}
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
            patch(
                "astrbot_plugin_imgexploration.core.result_sender.send_search_results",
                new=AsyncMock(),
            ) as send_results,
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
            download_thumbnails=True,
        )
        send_results.assert_awaited_once_with(event, [item])

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
        self.assertIn("策略 [SauceNAO] 返回 0 条结果", info_messages)
        self.assertNotIn(self.image_url, info_messages)
        self.assertNotIn(self.image_url[:50], info_messages)
        self.assertIn(self.image_url, debug_messages)
        strategy.search.assert_awaited_once_with(self.image_url)

    def test_proxy_log_omits_credentials(self) -> None:
        original_proxy = utils.get_proxy_url()
        self.addCleanup(utils.set_proxy_url, original_proxy)

        with patch.object(utils, "logger") as logger:
            utils.set_proxy_url("http://proxy-user:proxy-secret@proxy.example:7890")

        logged = _logged_text(logger)
        self.assertIn("http://proxy.example:7890", logged)
        self.assertNotIn("proxy-user", logged)
        self.assertNotIn("proxy-secret", logged)
        self.assertEqual(
            utils.get_proxy_url(),
            "http://proxy-user:proxy-secret@proxy.example:7890",
        )

    async def test_google_lens_errors_do_not_log_api_keys(self) -> None:
        api_keys = ["serpapi-secret-one", "serpapi-secret-two"]
        service = ImgExplorationService([GoogleLensStrategy(api_keys=api_keys)])

        with (
            patch(
                "astrbot_plugin_imgexploration.core.providers.google_lens_strategy.get_aiohttp_session",
                new=AsyncMock(return_value=_UrlLeakingSession()),
            ),
            patch(
                "astrbot_plugin_imgexploration.core.providers.google_lens_strategy.logger"
            ) as provider_logger,
            patch(
                "astrbot_plugin_imgexploration.core.service.logger"
            ) as service_logger,
        ):
            result = await service.explore(self.image_url)

        logged = _logged_text(provider_logger, service_logger)
        self.assertTrue(result.all_failed)
        self.assertIn("RuntimeError", logged)
        for api_key in api_keys:
            self.assertNotIn(api_key, logged)

    async def test_saucenao_errors_do_not_log_api_key_or_source(self) -> None:
        strategy = SauceNaoStrategy(api_key="saucenao-secret-key")
        service = ImgExplorationService([strategy])

        with (
            patch(
                "astrbot_plugin_imgexploration.core.providers.sauce_nao_strategy.get_aiohttp_session",
                new=AsyncMock(return_value=_UrlLeakingSession()),
            ),
            patch(
                "astrbot_plugin_imgexploration.core.providers.sauce_nao_strategy.logger"
            ) as provider_logger,
            patch(
                "astrbot_plugin_imgexploration.core.service.logger"
            ) as service_logger,
        ):
            result = await service.explore(self.image_url)
            with self.assertRaises(ProviderSearchError):
                await strategy.search("base64://private-image-content")
            with self.assertRaises(ProviderSearchError):
                await strategy.search("file:///a.png")

        logged = _logged_text(provider_logger, service_logger)
        self.assertTrue(result.all_failed)
        self.assertIn("RuntimeError", logged)
        self.assertNotIn("saucenao-secret-key", logged)
        self.assertIn("base64://pri***ontent", logged)
        self.assertNotIn("private-image-content", logged)
        self.assertIn("file:/***", logged)
        self.assertNotIn("a.png", logged)
