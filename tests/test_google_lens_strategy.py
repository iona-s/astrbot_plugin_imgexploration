"""Regression tests for SerpAPI key rotation."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import AsyncMock, patch

_STUB_MODULE_NAMES = (
    "plugin",
    "plugin.core",
    "plugin.core.constant",
    "plugin.core.models",
    "plugin.core.strategy",
    "plugin.core.utils",
    "plugin.core.providers",
    "plugin.core.providers.google_lens_strategy",
    "astrbot",
    "astrbot.api",
    "aiohttp",
)


class _Logger:
    def __getattr__(self, _name: str):
        return lambda *args, **kwargs: None


class _Response:
    def __init__(self, status: int, payload: dict | None = None) -> None:
        self.status = status
        self.payload = payload if payload is not None else {"visual_matches": []}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def text(self) -> str:
        return json.dumps(self.payload)


class _Session:
    def __init__(
        self,
        statuses: list[int],
        calls: list[str],
        payloads: list[dict] | None = None,
    ) -> None:
        self.statuses = iter(statuses)
        self.calls = calls
        self.payloads = iter(payloads or [])

    def get(self, url: str, **kwargs):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.calls.append(query["api_key"][0])
        return _Response(next(self.statuses), next(self.payloads, None))


def _load_google_lens_module():
    package = types.ModuleType("plugin")
    package.__path__ = []
    sys.modules["plugin"] = package

    core_package = types.ModuleType("plugin.core")
    core_package.__path__ = []
    sys.modules["plugin.core"] = core_package

    providers_package = types.ModuleType("plugin.core.providers")
    providers_package.__path__ = []
    sys.modules["plugin.core.providers"] = providers_package

    astrbot = types.ModuleType("astrbot")
    astrbot_api = types.ModuleType("astrbot.api")
    astrbot_api.logger = _Logger()
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = astrbot_api

    aiohttp = types.ModuleType("aiohttp")
    aiohttp.ClientTimeout = lambda **kwargs: kwargs
    sys.modules["aiohttp"] = aiohttp

    constant = types.ModuleType("plugin.core.constant")
    constant.HTTP_TIMEOUT_SECONDS = 5
    constant.SERPAPI_BASE_URL = "https://serpapi.com"
    sys.modules["plugin.core.constant"] = constant

    models = types.ModuleType("plugin.core.models")
    models.SearchResultItem = types.SimpleNamespace
    sys.modules["plugin.core.models"] = models

    strategy = types.ModuleType("plugin.core.strategy")
    strategy.ImageSearchStrategy = object
    sys.modules["plugin.core.strategy"] = strategy

    utils = types.ModuleType("plugin.core.utils")
    utils.get_aiohttp_session = None
    utils.download_bytes_batch = None
    utils.get_proxy_url = lambda: None
    sys.modules["plugin.core.utils"] = utils

    module_path = (
        Path(__file__).parents[1] / "core" / "providers" / "google_lens_strategy.py"
    )
    spec = importlib.util.spec_from_file_location(
        "plugin.core.providers.google_lens_strategy", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load google_lens_strategy.py")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class GoogleLensStrategyTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._saved_modules = {
            name: sys.modules.get(name) for name in _STUB_MODULE_NAMES
        }
        cls.module = _load_google_lens_module()

    @classmethod
    def tearDownClass(cls) -> None:
        for name, module in cls._saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def _strategy_with_statuses(
        self, statuses: list[int], payloads: list[dict] | None = None
    ):
        calls: list[str] = []
        session = _Session(statuses, calls, payloads)

        async def get_session():
            return session

        self.module.get_aiohttp_session = get_session
        self.module.get_proxy_url = lambda: None
        strategy = self.module.GoogleLensStrategy(["key-a", "key-b", "key-c"])
        return strategy, calls

    async def test_key_selection_rotates_after_each_pick(self) -> None:
        strategy = self.module.GoogleLensStrategy(["key-a", "key-b", "key-c"])

        picks = [await strategy._select_key_optimistically() for _ in range(4)]

        self.assertEqual(["key-a", "key-b", "key-c", "key-a"], picks)

    async def test_successful_searches_rotate_keys(self) -> None:
        strategy, calls = self._strategy_with_statuses([200, 200, 200])

        for _ in range(3):
            await strategy.search("https://example.com/image.jpg")

        self.assertEqual(["key-a", "key-b", "key-c"], calls)

    async def test_http_429_retries_with_next_key(self) -> None:
        strategy, calls = self._strategy_with_statuses([429, 200])

        await strategy.search("https://example.com/image.jpg")

        self.assertEqual(["key-a", "key-b"], calls)
        self.assertEqual(0, strategy._quota_cache["key-a"][0])

    async def test_http_429_tries_each_key_before_giving_up(self) -> None:
        strategy, calls = self._strategy_with_statuses([429, 429, 429])

        result = await strategy.search("https://example.com/image.jpg")

        self.assertEqual([], result)
        self.assertEqual(["key-a", "key-b", "key-c"], calls)

    async def test_successful_result_parsing_and_thumbnail_download(self) -> None:
        payload = {
            "visual_matches": [
                {
                    "title": "First Result",
                    "link": "https://source.example/1",
                    "source": "Example Source",
                    "thumbnail": "https://thumb.example/1.jpg",
                },
                {
                    "title": "Missing Link",
                    "thumbnail": "https://thumb.example/skipped.jpg",
                },
                {
                    "title": "Second Result",
                    "link": "https://source.example/2",
                    "source": "Another Source",
                    "thumbnail": "https://thumb.example/2.jpg",
                },
            ]
        }
        strategy, calls = self._strategy_with_statuses([200], [payload])
        download_thumbnails = AsyncMock(return_value=[b"thumb-1", None])
        with patch.object(
            self.module,
            "download_bytes_batch",
            download_thumbnails,
        ):
            results = await strategy.search("https://example.com/image.jpg")

        self.assertEqual(["key-a"], calls)
        self.assertEqual(2, len(results))
        self.assertEqual("First Result", results[0].title)
        self.assertEqual("https://source.example/1", results[0].url)
        self.assertEqual("Example Source", results[0].description)
        self.assertEqual(b"thumb-1", results[0].thumbnail_bytes)
        self.assertEqual("Second Result", results[1].title)
        self.assertIsNone(results[1].thumbnail_bytes)
        download_thumbnails.assert_awaited_once_with(
            ["https://thumb.example/1.jpg", "https://thumb.example/2.jpg"]
        )

    async def test_visual_matches_are_limited_to_eight(self) -> None:
        matches = [
            {
                "title": f"Result {index}",
                "link": f"https://source.example/{index}",
                "thumbnail": f"https://thumb.example/{index}.jpg",
            }
            for index in range(10)
        ]
        expected_thumbnail_urls = [
            f"https://thumb.example/{index}.jpg" for index in range(8)
        ]
        strategy, _ = self._strategy_with_statuses([200], [{"visual_matches": matches}])
        download_thumbnails = AsyncMock(return_value=[None] * 8)

        with patch.object(
            self.module,
            "download_bytes_batch",
            download_thumbnails,
        ):
            results = await strategy.search("https://example.com/image.jpg")

        self.assertEqual(
            [f"Result {index}" for index in range(8)],
            [result.title for result in results],
        )
        download_thumbnails.assert_awaited_once_with(expected_thumbnail_urls)

    async def test_non_quota_http_error_does_not_exhaust_or_retry_key(self) -> None:
        strategy, calls = self._strategy_with_statuses([500])

        result = await strategy.search("https://example.com/image.jpg")

        self.assertEqual([], result)
        self.assertEqual(["key-a"], calls)
        self.assertNotIn("key-a", strategy._quota_cache)

    async def test_quota_error_payload_retries_with_next_key(self) -> None:
        strategy, calls = self._strategy_with_statuses(
            [200, 200],
            [
                {"error": "API key has exceeded its quota"},
                {"visual_matches": []},
            ],
        )

        result = await strategy.search("https://example.com/image.jpg")

        self.assertEqual([], result)
        self.assertEqual(["key-a", "key-b"], calls)
        self.assertEqual(0, strategy._quota_cache["key-a"][0])

    async def test_service_name_and_search_validation(self) -> None:
        strategy_no_keys = self.module.GoogleLensStrategy([])
        self.assertEqual(strategy_no_keys.get_service_name(), "Google Lens")
        self.assertEqual(
            await strategy_no_keys.search("https://example.com/img.jpg"), []
        )

        strategy = self.module.GoogleLensStrategy(["key-a"])
        self.assertEqual(await strategy.search("base64://abc"), [])
        self.assertEqual(await strategy.search("file:///local.png"), [])
