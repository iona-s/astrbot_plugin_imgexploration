"""Google Lens provider behavior tests."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import Mock, patch

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
        statuses: list[int | Exception],
        calls: list[str],
        queries: list[dict[str, list[str]]],
        payloads: list[dict] | None = None,
    ) -> None:
        self.statuses = iter(statuses)
        self.calls = calls
        self.queries = queries
        self.payloads = iter(payloads or [])

    def get(self, url: str, **kwargs):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.calls.append(query["api_key"][0])
        self.queries.append(query)
        status = next(self.statuses)
        if isinstance(status, Exception):
            raise status
        return _Response(status, next(self.payloads, None))


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
    constant.DEFAULT_GOOGLE_LENS_MAX_RESULTS = 5
    constant.DEFAULT_GOOGLE_LENS_SEARCH_TYPE = "all"
    constant.DEFAULT_GOOGLE_LENS_LANGUAGE = "zh-cn"
    constant.DEFAULT_GOOGLE_LENS_COUNTRY = ""
    constant.DEFAULT_GOOGLE_LENS_SAFE_SEARCH = False
    constant.DEFAULT_GOOGLE_LENS_AUTO_CROP = False
    constant.DEFAULT_GOOGLE_LENS_NO_CACHE = False
    constant.HTTP_TIMEOUT_SECONDS = 5
    constant.SERPAPI_BASE_URL = "https://serpapi.com"
    sys.modules["plugin.core.constant"] = constant

    models = types.ModuleType("plugin.core.models")
    models.SearchResultItem = types.SimpleNamespace
    models.ProviderSearchError = type("ProviderSearchError", (Exception,), {})
    sys.modules["plugin.core.models"] = models

    strategy = types.ModuleType("plugin.core.strategy")
    strategy.ImageSearchStrategy = object
    sys.modules["plugin.core.strategy"] = strategy

    utils = types.ModuleType("plugin.core.utils")
    utils.get_aiohttp_session = None
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
        self,
        statuses: list[int | Exception],
        payloads: list[dict] | None = None,
        *,
        max_results: int = 5,
        **strategy_options,
    ):
        calls: list[str] = []
        queries: list[dict[str, list[str]]] = []
        session = _Session(statuses, calls, queries, payloads)
        self.request_queries = queries

        async def get_session():
            return session

        self.module.get_aiohttp_session = get_session
        self.module.get_proxy_url = lambda: None
        strategy = self.module.GoogleLensStrategy(
            api_keys=["key-a", "key-b", "key-c"],
            max_results=max_results,
            **strategy_options,
        )
        return strategy, calls

    async def test_successful_searches_rotate_keys(self) -> None:
        strategy, calls = self._strategy_with_statuses([200, 200, 200])

        for _ in range(3):
            await strategy.search("https://example.com/image.jpg")

        self.assertEqual(["key-a", "key-b", "key-c"], calls)

    async def test_http_429_keeps_exhausted_key_out_of_later_searches(self) -> None:
        strategy, calls = self._strategy_with_statuses([429, 200, 200, 200])

        for _ in range(3):
            await strategy.search("https://example.com/image.jpg")

        self.assertEqual(["key-a", "key-b", "key-c", "key-b"], calls)

    async def test_http_429_tries_each_key_before_giving_up(self) -> None:
        strategy, calls = self._strategy_with_statuses([429, 429, 429])

        with self.assertRaises(self.module.ProviderSearchError):
            await strategy.search("https://example.com/image.jpg")

        self.assertEqual(["key-a", "key-b", "key-c"], calls)

    async def test_successful_result_parsing_keeps_thumbnail_urls(self) -> None:
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

        results = await strategy.search("https://example.com/image.jpg")

        self.assertEqual(["key-a"], calls)
        self.assertEqual(2, len(results))
        self.assertEqual("First Result", results[0].title)
        self.assertEqual("https://source.example/1", results[0].url)
        self.assertEqual("Example Source", results[0].description)
        self.assertEqual("Second Result", results[1].title)
        # 缩略图只保留 URL，由服务层统一下载
        self.assertEqual(
            ["https://thumb.example/1.jpg", "https://thumb.example/2.jpg"],
            [result.thumbnail for result in results],
        )
        self.assertEqual([None, None], [result.thumbnail_bytes for result in results])

    async def test_visual_matches_use_configured_limit(self) -> None:
        matches = [
            {
                "title": f"Result {index}",
                "link": f"https://source.example/{index}",
                "thumbnail": f"https://thumb.example/{index}.jpg",
            }
            for index in range(10)
        ]
        strategy, _ = self._strategy_with_statuses(
            [200],
            [{"visual_matches": matches}],
            max_results=3,
        )

        results = await strategy.search("https://example.com/image.jpg")

        self.assertEqual(
            [f"Result {index}" for index in range(3)],
            [result.title for result in results],
        )

    async def test_invalid_matches_do_not_count_toward_limit(self) -> None:
        matches = [
            {"title": "Result 0", "link": "https://source.example/0"},
            {"title": "Missing Link"},
            {"title": "Result 1", "link": "https://source.example/1"},
            {"title": "Result 2", "link": "https://source.example/2"},
        ]
        strategy, _ = self._strategy_with_statuses(
            [200],
            [{"visual_matches": matches}],
            max_results=2,
        )

        results = await strategy.search("https://example.com/image.jpg")

        self.assertEqual(["Result 0", "Result 1"], [result.title for result in results])

    async def test_search_options_are_sent_to_serpapi(self) -> None:
        strategy, _ = self._strategy_with_statuses(
            [200],
            search_type="exact_matches",
            language="ja",
            country="jp",
            safe_search=True,
            auto_crop=True,
            no_cache=True,
        )

        await strategy.search("https://example.com/image.jpg")

        query = self.request_queries[0]
        self.assertEqual(["exact_matches"], query["type"])
        self.assertEqual(["ja"], query["hl"])
        self.assertEqual(["jp"], query["country"])
        self.assertEqual(["active"], query["safe"])
        self.assertEqual(["true"], query["auto_crop"])
        self.assertEqual(["true"], query["no_cache"])

    async def test_string_boolean_options_are_normalized(self) -> None:
        strategy, _ = self._strategy_with_statuses(
            [200],
            safe_search="false",
            auto_crop="false",
            no_cache="false",
        )

        await strategy.search("https://example.com/image.jpg")

        query = self.request_queries[0]
        self.assertNotIn("safe", query)
        self.assertEqual(["false"], query["auto_crop"])
        self.assertEqual(["false"], query["no_cache"])

    async def test_exact_matches_response_is_parsed(self) -> None:
        payload = {
            "exact_matches": [
                {
                    "title": "Original Source",
                    "link": "https://source.example/original",
                    "source": "Example",
                    "thumbnail": "https://thumb.example/original.jpg",
                }
            ],
            "visual_matches": [
                {
                    "title": "Visual Match",
                    "link": "https://source.example/visual",
                }
            ],
        }
        strategy, _ = self._strategy_with_statuses(
            [200],
            [payload],
            search_type="exact_matches",
        )

        results = await strategy.search("https://example.com/image.jpg")

        self.assertEqual(1, len(results))
        self.assertEqual("Original Source", results[0].title)
        self.assertEqual("https://source.example/original", results[0].url)
        self.assertEqual("https://thumb.example/original.jpg", results[0].thumbnail)
        self.assertIsNone(results[0].thumbnail_bytes)

    async def test_invalid_search_type_falls_back_to_default(self) -> None:
        strategy, _ = self._strategy_with_statuses([200], search_type="invalid")

        await strategy.search("https://example.com/image.jpg")

        self.assertEqual("all", strategy.search_type)

    async def test_products_response_uses_visual_matches(self) -> None:
        payload = {
            "visual_matches": [
                {
                    "title": "Product Match",
                    "link": "https://shop.example/product",
                }
            ]
        }
        strategy, _ = self._strategy_with_statuses(
            [200],
            [payload],
            search_type="products",
        )

        results = await strategy.search("https://example.com/image.jpg")

        self.assertEqual(["Product Match"], [result.title for result in results])

    async def test_exhausted_keys_fail_without_retrying_each_key(self) -> None:
        strategy, calls = self._strategy_with_statuses([429, 429, 429])
        with self.assertRaises(self.module.ProviderSearchError):
            await strategy.search("https://example.com/image.jpg")

        logger = Mock()
        with (
            patch.object(self.module, "logger", logger),
            self.assertRaises(self.module.ProviderSearchError),
        ):
            await strategy.search("https://example.com/image.jpg")

        self.assertEqual(["key-a", "key-b", "key-c"], calls)
        logger.warning.assert_not_called()

    async def test_non_quota_http_error_fails_without_exhausting_key(self) -> None:
        strategy, calls = self._strategy_with_statuses([500, 200, 200, 200])

        with self.assertRaises(self.module.ProviderSearchError):
            await strategy.search("https://example.com/image.jpg")

        self.assertEqual(["key-a"], calls)
        for _ in range(3):
            self.assertEqual([], await strategy.search("https://example.com/image.jpg"))
        self.assertEqual(["key-a", "key-b", "key-c", "key-a"], calls)

    async def test_network_error_does_not_try_other_keys(self) -> None:
        strategy, calls = self._strategy_with_statuses([TimeoutError()])

        with self.assertRaises(self.module.ProviderSearchError):
            await strategy.search("https://example.com/image.jpg")

        self.assertEqual(["key-a"], calls)

    async def test_no_results_error_returns_empty_result_without_retry(self) -> None:
        strategy, calls = self._strategy_with_statuses(
            [200],
            [{"error": "Google Lens hasn't returned any results for this query."}],
        )

        result = await strategy.search("https://example.com/image.jpg")

        self.assertEqual([], result)
        self.assertEqual(["key-a"], calls)

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

    async def test_service_name_and_search_validation(self) -> None:
        strategy_no_keys = self.module.GoogleLensStrategy(api_keys=[])
        self.assertEqual(strategy_no_keys.get_service_name(), "Google Lens")
        with self.assertRaises(self.module.ProviderSearchError):
            await strategy_no_keys.search("https://example.com/img.jpg")

        strategy = self.module.GoogleLensStrategy(api_keys=["key-a"])
        with self.assertRaises(self.module.ProviderSearchError):
            await strategy.search("base64://abc")
        with self.assertRaises(self.module.ProviderSearchError):
            await strategy.search("file:///local.png")
