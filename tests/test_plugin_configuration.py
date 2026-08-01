from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import DEFAULT, patch

from astrbot_plugin_imgexploration.main import ImgExplorationPlugin

from tests.helpers import PluginTestCase


class _DummyMapping:
    def __init__(self, data: dict) -> None:
        self._data = data

    def items(self):
        return self._data.items()


class _DummyIterable:
    def __init__(self, data: dict) -> None:
        self._data = data

    def __iter__(self):
        return iter(self._data)

    def __getitem__(self, item):
        return self._data[item]


class PluginConfigurationTests(PluginTestCase):
    @staticmethod
    def _patch_strategy_dependencies():
        return patch.multiple(
            "astrbot_plugin_imgexploration.main",
            set_proxy_url=DEFAULT,
            set_user_agent=DEFAULT,
            set_allow_image_upload=DEFAULT,
            set_allow_local_file_access=DEFAULT,
            init_image_context_manager=DEFAULT,
            SauceNaoStrategy=DEFAULT,
            GoogleLensStrategy=DEFAULT,
            Ascii2dStrategy=DEFAULT,
        )

    def test_config_to_dict_conversion(self) -> None:
        # dict input
        d = {"a": 1}
        self.assertEqual(ImgExplorationPlugin._config_to_dict(d), {"a": 1})

        # mapping with .items()
        m = _DummyMapping({"b": 2})
        self.assertEqual(ImgExplorationPlugin._config_to_dict(m), {"b": 2})

        # iterable mapping
        it = _DummyIterable({"c": 3})
        self.assertEqual(ImgExplorationPlugin._config_to_dict(it), {"c": 3})

        # invalid object
        bad = object()
        self.assertEqual(ImgExplorationPlugin._config_to_dict(bad), {})

    def test_get_nested_config(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        plugin.config = {
            "network": {"proxy_url": "http://127.0.0.1:7890"},
            "empty": None,
        }

        self.assertEqual(
            plugin._get_nested_config("network", "proxy_url"),
            "http://127.0.0.1:7890",
        )
        self.assertEqual(
            plugin._get_nested_config("network", "missing", default="def"),
            "def",
        )
        self.assertEqual(
            plugin._get_nested_config("non_existent", "key", default=123),
            123,
        )
        self.assertEqual(
            plugin._get_nested_config("empty", "key", default="fallback"),
            "fallback",
        )

    def test_init_strategies_combinations(self) -> None:
        # 1. All strategies enabled with valid keys/configs
        conf_all = {
            "network": {
                "proxy_url": "http://127.0.0.1:7890",
                "user_agent": "TestAgent/1.0",
                "allow_image_upload": False,
                "allow_local_file_access": True,
            },
            "ai_behavior": {
                "image_context_isolation": "global",
                "max_images_per_session": 8,
                "image_context_ttl_seconds": 120,
                "max_image_context_sessions": 16,
                "include_image_url_in_context": False,
            },
            "strategies": {
                "enable_saucenao": True,
                "enable_google_lens": True,
                "enable_ascii2d": True,
                "saucenao_similarity_threshold": 65,
            },
            "api_keys": {
                "saucenao_api_key": "sn_key",
                "serpapi_keys": ["google_key1"],
                "ascii2d_session_id": "ascii_sess",
                "ascii2d_cf_clearance": "cf_token",
            },
        }

        plugin_all = self.make_plugin(SimpleNamespace())
        plugin_all.config = conf_all
        plugin_all.strategies = []
        with self._patch_strategy_dependencies() as dependencies:
            plugin_all._init_strategies()

        dependencies["set_proxy_url"].assert_called_once_with("http://127.0.0.1:7890")
        dependencies["set_user_agent"].assert_called_once_with("TestAgent/1.0")
        dependencies["set_allow_image_upload"].assert_called_once_with(False)
        dependencies["set_allow_local_file_access"].assert_called_once_with(True)
        dependencies["init_image_context_manager"].assert_called_once_with(
            isolation_mode="global",
            max_images=8,
            ttl_seconds=120,
            max_sessions=16,
            include_url_in_context=False,
        )
        dependencies["SauceNaoStrategy"].assert_called_once_with(
            api_key="sn_key", similarity_threshold=65
        )
        dependencies["GoogleLensStrategy"].assert_called_once_with(
            api_keys=["google_key1"]
        )
        dependencies["Ascii2dStrategy"].assert_called_once_with(
            session_id="ascii_sess", cf_clearance="cf_token"
        )
        self.assertEqual(
            plugin_all.strategies,
            [
                dependencies["SauceNaoStrategy"].return_value,
                dependencies["GoogleLensStrategy"].return_value,
                dependencies["Ascii2dStrategy"].return_value,
            ],
        )

        # 2. Strategies enabled but missing keys
        conf_missing_keys = {
            "strategies": {
                "enable_saucenao": True,
                "enable_google_lens": True,
                "enable_ascii2d": True,
            },
            "api_keys": {},
        }
        plugin_no_keys = self.make_plugin(SimpleNamespace())
        plugin_no_keys.config = conf_missing_keys
        plugin_no_keys.strategies = []
        with self._patch_strategy_dependencies() as dependencies:
            plugin_no_keys._init_strategies()

        self.assertEqual(len(plugin_no_keys.strategies), 0)
        dependencies["SauceNaoStrategy"].assert_not_called()
        dependencies["GoogleLensStrategy"].assert_not_called()
        dependencies["Ascii2dStrategy"].assert_not_called()

        # 3. All strategies disabled
        conf_disabled = {
            "strategies": {
                "enable_saucenao": False,
                "enable_google_lens": False,
                "enable_ascii2d": False,
            },
        }
        plugin_disabled = self.make_plugin(SimpleNamespace())
        plugin_disabled.config = conf_disabled
        plugin_disabled.strategies = []
        with self._patch_strategy_dependencies() as dependencies:
            plugin_disabled._init_strategies()

        self.assertEqual(len(plugin_disabled.strategies), 0)
        dependencies["SauceNaoStrategy"].assert_not_called()
        dependencies["GoogleLensStrategy"].assert_not_called()
        dependencies["Ascii2dStrategy"].assert_not_called()
