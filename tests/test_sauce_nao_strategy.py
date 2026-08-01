from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from astrbot_plugin_imgexploration.core.providers.sauce_nao_strategy import (
    SauceNaoStrategy,
)


class SauceNaoStrategyTests(unittest.IsolatedAsyncioTestCase):
    def test_init_and_service_name(self) -> None:
        with self.assertRaises(TypeError):
            SauceNaoStrategy("my_key")

        strategy = SauceNaoStrategy(api_key="my_key", similarity_threshold=150)
        self.assertEqual(strategy.get_service_name(), "SauceNAO")
        self.assertEqual(strategy.similarity_threshold, 100)
        self.assertEqual(strategy.max_results, 3)

        strategy_low = SauceNaoStrategy(
            api_key="my_key",
            similarity_threshold=-10,
            max_results=7,
        )
        self.assertEqual(strategy_low.similarity_threshold, 0)
        self.assertEqual(strategy_low.max_results, 7)

    async def test_search_validation_failures(self) -> None:
        strategy_no_key = SauceNaoStrategy(api_key=None)
        self.assertEqual(
            await strategy_no_key.search("https://example.com/img.jpg"), []
        )

        strategy = SauceNaoStrategy(api_key="my_key")
        self.assertEqual(await strategy.search("base64://abc"), [])
        self.assertEqual(await strategy.search("file:///local/path.jpg"), [])

    async def test_search_api_http_error(self) -> None:
        strategy = SauceNaoStrategy(api_key="valid_key")
        session_mock = MagicMock()
        context_mock = AsyncMock()

        resp_500 = AsyncMock()
        resp_500.status = 500
        context_mock.__aenter__.return_value = resp_500
        session_mock.get.return_value = context_mock

        with patch(
            "astrbot_plugin_imgexploration.core.providers.sauce_nao_strategy.get_aiohttp_session",
            return_value=session_mock,
        ):
            results = await strategy.search("https://example.com/img.jpg")
            self.assertEqual(results, [])

    async def test_search_api_error_response(self) -> None:
        strategy = SauceNaoStrategy(api_key="valid_key")
        session_mock = MagicMock()
        context_mock = AsyncMock()

        resp_error = AsyncMock()
        resp_error.status = 200
        resp_error.text = AsyncMock(
            return_value=json.dumps(
                {"header": {"status": -1, "message": "Invalid API Key"}}
            )
        )
        context_mock.__aenter__.return_value = resp_error
        session_mock.get.return_value = context_mock

        with patch(
            "astrbot_plugin_imgexploration.core.providers.sauce_nao_strategy.get_aiohttp_session",
            return_value=session_mock,
        ):
            results = await strategy.search("https://example.com/img.jpg")
            self.assertEqual(results, [])

    async def test_search_success_parsing_and_threshold_filtering(self) -> None:
        strategy = SauceNaoStrategy(api_key="valid_key", similarity_threshold=60)
        session_mock = MagicMock()
        context_mock = AsyncMock()

        api_payload = {
            "results": [
                {
                    "header": {
                        "similarity": "85.5",
                        "thumbnail": "https://saucenao.com/thumb1.jpg",
                    },
                    "data": {
                        "title": "Sample Title",
                        "ext_urls": ["https://pixiv.net/art/1"],
                    },
                },
                {
                    "header": {
                        "similarity": "70.0",
                        "thumbnail": "https://saucenao.com/thumb2.jpg",
                    },
                    "data": {
                        "member_name": "ArtistBob",
                        "ext_urls": ["https://pixiv.net/art/2"],
                    },
                },
                {
                    "header": {
                        "similarity": "50.0",
                        "thumbnail": "https://saucenao.com/thumb3.jpg",
                    },
                    "data": {
                        "title": "Filtered Result",
                        "ext_urls": ["https://pixiv.net/art/3"],
                    },
                },
            ]
        }

        resp_ok = AsyncMock()
        resp_ok.status = 200
        resp_ok.text = AsyncMock(return_value=json.dumps(api_payload))
        context_mock.__aenter__.return_value = resp_ok
        session_mock.get.return_value = context_mock

        with patch(
            "astrbot_plugin_imgexploration.core.providers.sauce_nao_strategy.get_aiohttp_session",
            return_value=session_mock,
        ):
            results = await strategy.search("https://example.com/img.jpg")

            self.assertEqual(len(results), 2)
            self.assertEqual(results[0].title, "Sample Title")
            self.assertEqual(results[0].url, "https://pixiv.net/art/1")
            self.assertEqual(results[0].similarity, "85.50%")
            self.assertEqual(results[1].title, "Artist: ArtistBob")
            self.assertEqual(
                session_mock.get.call_args.kwargs["params"]["numres"],
                "3",
            )

    async def test_search_uses_configured_result_limit(self) -> None:
        strategy = SauceNaoStrategy(api_key="valid_key", max_results=1)
        session_mock = MagicMock()
        context_mock = AsyncMock()
        api_payload = {
            "results": [
                {
                    "header": {"similarity": "90", "thumbnail": "thumb-1"},
                    "data": {"title": "First", "ext_urls": ["source-1"]},
                },
                {
                    "header": {"similarity": "80", "thumbnail": "thumb-2"},
                    "data": {"title": "Second", "ext_urls": ["source-2"]},
                },
            ]
        }
        response = AsyncMock()
        response.status = 200
        response.text = AsyncMock(return_value=json.dumps(api_payload))
        context_mock.__aenter__.return_value = response
        session_mock.get.return_value = context_mock

        with patch(
            "astrbot_plugin_imgexploration.core.providers.sauce_nao_strategy.get_aiohttp_session",
            return_value=session_mock,
        ):
            results = await strategy.search("https://example.com/img.jpg")

        self.assertEqual(["First"], [result.title for result in results])
        self.assertEqual(
            session_mock.get.call_args.kwargs["params"]["numres"],
            "1",
        )

    def test_extract_title_priorities(self) -> None:
        extract = SauceNaoStrategy._extract_title

        self.assertEqual(extract({"title": "T1", "eng_name": "T2"}), "T1")
        self.assertEqual(extract({"eng_name": "E1", "jp_name": "J1"}), "E1")
        self.assertEqual(extract({"jp_name": "J1", "material": "M1"}), "J1")
        self.assertEqual(extract({"material": "M1", "source": "S1"}), "M1")
        self.assertEqual(extract({"source": "S1"}), "S1")
        self.assertEqual(extract({"member_name": "Alice"}), "Artist: Alice")
        self.assertEqual(extract({}), "SauceNAO Result")
