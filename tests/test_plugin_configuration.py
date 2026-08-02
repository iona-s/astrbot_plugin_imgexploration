from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import DEFAULT, patch

from astrbot_plugin_imgexploration.core.constant import (
    DEFAULT_ASCII2D_BOVW_MAX_RESULTS,
    DEFAULT_ASCII2D_COLOR_MAX_RESULTS,
    DEFAULT_GOOGLE_LENS_MAX_RESULTS,
    DEFAULT_SAUCENAO_MAX_RESULTS,
)
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

    def test_result_limit_normalization(self) -> None:
        for value in (None, "", 0, -1, True, False, "invalid", object()):
            with self.subTest(value=value):
                self.assertEqual(
                    ImgExplorationPlugin._normalize_result_limit(value, 7),
                    7,
                )

        self.assertEqual(4, ImgExplorationPlugin._normalize_result_limit(4, 7))
        self.assertEqual(6, ImgExplorationPlugin._normalize_result_limit("6", 7))

    def test_result_limit_schema_matches_code_defaults(self) -> None:
        schema_path = Path(__file__).parents[1] / "_conf_schema.json"
        display_items = json.loads(schema_path.read_text(encoding="utf-8"))["display"][
            "items"
        ]

        self.assertNotIn("max_results", display_items)
        self.assertEqual(
            {
                "saucenao_max_results": DEFAULT_SAUCENAO_MAX_RESULTS,
                "google_lens_max_results": DEFAULT_GOOGLE_LENS_MAX_RESULTS,
                "ascii2d_bovw_max_results": DEFAULT_ASCII2D_BOVW_MAX_RESULTS,
                "ascii2d_color_max_results": DEFAULT_ASCII2D_COLOR_MAX_RESULTS,
            },
            {key: item["default"] for key, item in display_items.items()},
        )

    def test_llm_tool_enablement_schema_matches_runtime_default(self) -> None:
        schema_path = Path(__file__).parents[1] / "_conf_schema.json"
        ai_behavior_items = json.loads(schema_path.read_text(encoding="utf-8"))[
            "ai_behavior"
        ]["items"]
        plugin = self.make_plugin(SimpleNamespace())
        plugin.config = {}

        self.assertTrue(ai_behavior_items["enable_llm_tools"]["default"])
        self.assertTrue(plugin._are_llm_tools_enabled())

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
            "display": {
                "saucenao_max_results": 4,
                "google_lens_max_results": 6,
                "ascii2d_bovw_max_results": 7,
                "ascii2d_color_max_results": 1,
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
            api_key="sn_key",
            similarity_threshold=65,
            max_results=4,
        )
        dependencies["GoogleLensStrategy"].assert_called_once_with(
            api_keys=["google_key1"],
            max_results=6,
        )
        dependencies["Ascii2dStrategy"].assert_called_once_with(
            session_id="ascii_sess",
            cf_clearance="cf_token",
            bovw_max_results=7,
            color_max_results=1,
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

    def test_init_strategies_uses_defaults_for_invalid_limits(self) -> None:
        base_config = {
            "strategies": {
                "enable_saucenao": True,
                "enable_google_lens": True,
                "enable_ascii2d": True,
            },
            "api_keys": {
                "saucenao_api_key": "sn_key",
                "serpapi_keys": ["google_key"],
                "ascii2d_session_id": "ascii_sess",
            },
        }
        display_values = (
            None,
            {},
            {
                "saucenao_max_results": 0,
                "google_lens_max_results": "",
                "ascii2d_bovw_max_results": False,
                "ascii2d_color_max_results": -2,
            },
            "invalid",
        )

        for display in display_values:
            with self.subTest(display=display):
                plugin = self.make_plugin(SimpleNamespace())
                plugin.config = dict(base_config)
                if display is not None:
                    plugin.config["display"] = display
                plugin.strategies = []

                with self._patch_strategy_dependencies() as dependencies:
                    plugin._init_strategies()

                dependencies["SauceNaoStrategy"].assert_called_once_with(
                    api_key="sn_key",
                    similarity_threshold=40,
                    max_results=DEFAULT_SAUCENAO_MAX_RESULTS,
                )
                dependencies["GoogleLensStrategy"].assert_called_once_with(
                    api_keys=["google_key"],
                    max_results=DEFAULT_GOOGLE_LENS_MAX_RESULTS,
                )
                dependencies["Ascii2dStrategy"].assert_called_once_with(
                    session_id="ascii_sess",
                    cf_clearance="",
                    bovw_max_results=DEFAULT_ASCII2D_BOVW_MAX_RESULTS,
                    color_max_results=DEFAULT_ASCII2D_COLOR_MAX_RESULTS,
                )
