from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from astrbot_plugin_imgexploration.core.providers.ascii2d_strategy import (
    Ascii2dStrategy,
)


class Ascii2dStrategyTests(unittest.IsolatedAsyncioTestCase):
    def test_init_and_cookies_and_proxies(self) -> None:
        with self.assertRaises(TypeError):
            Ascii2dStrategy("sess_123")

        strategy = Ascii2dStrategy(session_id="sess_123", cf_clearance="cf_abc")
        self.assertEqual(strategy.get_service_name(), "Ascii2d")
        self.assertEqual(strategy.bovw_max_results, 3)
        self.assertEqual(strategy.color_max_results, 2)

        custom_strategy = Ascii2dStrategy(
            bovw_max_results=4,
            color_max_results=1,
        )
        self.assertEqual(custom_strategy.bovw_max_results, 4)
        self.assertEqual(custom_strategy.color_max_results, 1)

        cookies = strategy._get_cookies()
        self.assertEqual(cookies, {"_session_id": "sess_123", "cf_clearance": "cf_abc"})

        with patch(
            "astrbot_plugin_imgexploration.core.providers.ascii2d_strategy.get_proxy_url",
            return_value="http://127.0.0.1:7890",
        ):
            self.assertEqual(
                strategy._get_proxies(),
                {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"},
            )

        with patch(
            "astrbot_plugin_imgexploration.core.providers.ascii2d_strategy.get_proxy_url",
            return_value=None,
        ):
            self.assertIsNone(strategy._get_proxies())

    async def test_search_validation_failures(self) -> None:
        strategy = Ascii2dStrategy()
        self.assertEqual(await strategy.search("base64://abc"), [])
        self.assertEqual(await strategy.search("file:///local.jpg"), [])

    async def test_session_lifecycle_and_close(self) -> None:
        strategy = Ascii2dStrategy()
        session_mock = MagicMock()
        session_mock.close = AsyncMock()

        with patch(
            "astrbot_plugin_imgexploration.core.providers.ascii2d_strategy.AsyncSession",
            return_value=session_mock,
        ):
            s1 = await strategy._get_session()
            s2 = await strategy._get_session()
            self.assertIs(s1, s2)

            await strategy.close()
            session_mock.close.assert_awaited_once()
            self.assertIsNone(strategy._session)

    async def test_fetch_authenticity_token_success_and_failure(self) -> None:
        strategy = Ascii2dStrategy()
        session_mock = MagicMock()

        # Success case
        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.text = '<form><input type="hidden" name="authenticity_token" value="secret_token_123"/></form>'
        session_mock.get = AsyncMock(return_value=resp_ok)

        with patch.object(strategy, "_get_session", return_value=session_mock):
            token = await strategy._fetch_authenticity_token()
            self.assertEqual(token, "secret_token_123")

        # Failure HTTP case
        resp_500 = MagicMock()
        resp_500.status_code = 500
        resp_500.text = "Internal Server Error"
        session_mock.get = AsyncMock(return_value=resp_500)

        with patch.object(strategy, "_get_session", return_value=session_mock):
            self.assertIsNone(await strategy._fetch_authenticity_token())

        # No token in HTML case
        resp_no_token = MagicMock()
        resp_no_token.status_code = 200
        resp_no_token.text = "<html><body>No token here</body></html>"
        session_mock.get = AsyncMock(return_value=resp_no_token)

        with patch.object(strategy, "_get_session", return_value=session_mock):
            self.assertIsNone(await strategy._fetch_authenticity_token())

    async def test_post_url_search_success_and_failure(self) -> None:
        strategy = Ascii2dStrategy()
        session_mock = MagicMock()

        # Success redirect case
        resp_redirect = MagicMock()
        resp_redirect.status_code = 200
        resp_redirect.url = "https://ascii2d.net/search/color/1234567890abcdef"
        session_mock.post = AsyncMock(return_value=resp_redirect)

        with patch.object(strategy, "_get_session", return_value=session_mock):
            url = await strategy._post_url_search(
                "https://example.com/img.jpg", "token_123"
            )
            self.assertEqual(url, "https://ascii2d.net/search/color/1234567890abcdef")

        # Non-200 case
        resp_403 = MagicMock()
        resp_403.status_code = 403
        resp_403.text = "Forbidden"
        session_mock.post = AsyncMock(return_value=resp_403)

        with patch.object(strategy, "_get_session", return_value=session_mock):
            self.assertIsNone(
                await strategy._post_url_search(
                    "https://example.com/img.jpg", "token_123"
                )
            )

    def test_parse_ascii2d_html(self) -> None:
        sample_html = """
        <div class="row item-box">
            <!-- Search original image (ignored) -->
            <img src="/images/original.jpg"/>
            <h6><a href="/search">Original Image</a></h6>
            <div class="clearfix"></div>
        </div>
        <div class="row item-box">
            <img src="/uploads/thumb1.jpg"/>
            <h6><a href="https://pixiv.net/art/100">Pixiv Artwork 100</a></h6>
            <div class="clearfix"></div>
        </div>
        <div class="row item-box">
            <img src="https://external.com/thumb2.jpg"/>
            <h6><a href="/detail/link">Relative Link</a></h6>
            <small><a href="https://twitter.com/user/status/1">Twitter Post</a></small>
            <div class="clearfix"></div>
        </div>
        """

        results = Ascii2dStrategy._parse_ascii2d_html(sample_html)
        self.assertEqual(len(results), 2)

        self.assertEqual(results[0].title, "Pixiv Artwork 100")
        self.assertEqual(results[0].url, "https://pixiv.net/art/100")
        self.assertEqual(results[0].thumbnail, "https://ascii2d.net/uploads/thumb1.jpg")

        self.assertEqual(results[1].title, "Relative Link")
        self.assertEqual(results[1].url, "https://twitter.com/user/status/1")
        self.assertEqual(results[1].thumbnail, "https://external.com/thumb2.jpg")

    async def test_search_end_to_end_flow(self) -> None:
        strategy = Ascii2dStrategy(bovw_max_results=2, color_max_results=1)

        color_results = [
            MagicMock(
                title=f"Color Result {index}",
                url=f"https://source.com/color/{index}",
                thumbnail=f"https://thumb/c/{index}",
            )
            for index in range(2)
        ]
        bovw_results = [
            MagicMock(
                title=f"BOVW Result {index}",
                url=f"https://source.com/bovw/{index}",
                thumbnail=f"https://thumb/b/{index}",
            )
            for index in range(3)
        ]

        with (
            patch.object(
                strategy, "_fetch_authenticity_token", return_value="token123"
            ),
            patch.object(
                strategy,
                "_post_url_search",
                return_value="https://ascii2d.net/search/color/hash",
            ),
            patch.object(
                strategy,
                "_fetch_and_parse_result_page",
                side_effect=[color_results, bovw_results],
            ),
            patch(
                "astrbot_plugin_imgexploration.core.providers.ascii2d_strategy.download_bytes",
                side_effect=[b"bovw_0", b"bovw_1", b"color_0"],
            ),
        ):
            results = await strategy.search("https://example.com/target.png")
            self.assertEqual(
                ["BOVW Result 0", "BOVW Result 1", "Color Result 0"],
                [result.title for result in results],
            )
            self.assertEqual(
                [b"bovw_0", b"bovw_1", b"color_0"],
                [result.thumbnail_bytes for result in results],
            )
