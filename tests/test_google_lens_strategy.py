"""Regression tests for SerpAPI key rotation."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
import urllib.parse
from pathlib import Path

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
    def __init__(self, status: int) -> None:
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def text(self) -> str:
        return json.dumps({"visual_matches": []})


class _Session:
    def __init__(self, statuses: list[int], calls: list[str]) -> None:
        self.statuses = iter(statuses)
        self.calls = calls

    def get(self, url: str, **kwargs):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.calls.append(query["api_key"][0])
        return _Response(next(self.statuses))


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
    models.SearchResultItem = object
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

    def _strategy_with_statuses(self, statuses: list[int]):
        calls: list[str] = []
        session = _Session(statuses, calls)

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


if __name__ == "__main__":
    unittest.main()
