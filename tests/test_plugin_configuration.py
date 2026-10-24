from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import DEFAULT, Mock, patch

from astrbot_plugin_imgexploration.core.constant import (
    DEFAULT_ALLOW_IMAGE_UPLOAD,
    DEFAULT_ALLOW_LOCAL_FILE_ACCESS,
    DEFAULT_ASCII2D_BOVW_MAX_RESULTS,
    DEFAULT_ASCII2D_COLOR_MAX_RESULTS,
    DEFAULT_GOOGLE_LENS_AUTO_CROP,
    DEFAULT_GOOGLE_LENS_COUNTRY,
    DEFAULT_GOOGLE_LENS_LANGUAGE,
    DEFAULT_GOOGLE_LENS_MAX_RESULTS,
    DEFAULT_GOOGLE_LENS_NO_CACHE,
    DEFAULT_GOOGLE_LENS_SAFE_SEARCH,
    DEFAULT_GOOGLE_LENS_SEARCH_TYPE,
    DEFAULT_IMAGE_CONTEXT_ISOLATION,
    DEFAULT_IMAGE_CONTEXT_TTL_SECONDS,
    DEFAULT_LLM_TOOL_RESPONSE_MODE,
    DEFAULT_MAX_IMAGE_CONTEXT_SESSIONS,
    DEFAULT_MAX_IMAGES_PER_SESSION,
    DEFAULT_SAUCENAO_MAX_RESULTS,
    DEFAULT_SAUCENAO_SIMILARITY_THRESHOLD,
    DEFAULT_TRACE_MOE_MAX_RESULTS,
)
from astrbot_plugin_imgexploration.main import (
    _ENGINE_CONFIG_VERSION,
    _LEGACY_ENGINE_CONFIG_KEYS,
    ImgExplorationPlugin,
)

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


class _SaveableConfig(dict):
    def __init__(self, data: dict) -> None:
        super().__init__(data)
        self.save_config = Mock()


class PluginConfigurationTests(PluginTestCase):
    @staticmethod
    def _patch_strategy_dependencies():
        return patch.multiple(
            "astrbot_plugin_imgexploration.main",
            set_proxy_url=DEFAULT,
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
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        expected = {
            ("saucenao", "max_results"): DEFAULT_SAUCENAO_MAX_RESULTS,
            ("google_lens", "max_results"): DEFAULT_GOOGLE_LENS_MAX_RESULTS,
            ("ascii2d", "bovw_max_results"): DEFAULT_ASCII2D_BOVW_MAX_RESULTS,
            ("ascii2d", "color_max_results"): DEFAULT_ASCII2D_COLOR_MAX_RESULTS,
            ("trace_moe", "max_results"): DEFAULT_TRACE_MOE_MAX_RESULTS,
        }

        self.assertEqual(
            expected,
            {
                (group, key): schema[group]["items"][key]["default"]
                for group, key in expected
            },
        )

    def test_legacy_engine_schema_matches_engine_groups(self) -> None:
        schema_path = Path(__file__).parents[1] / "_conf_schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        # 每个引擎配置项都有对应的旧配置，且新旧默认值一致
        self.assertEqual(
            {
                (group, key)
                for group in ("saucenao", "google_lens", "ascii2d", "trace_moe")
                for key in schema[group]["items"]
            },
            set(_LEGACY_ENGINE_CONFIG_KEYS.values()),
        )
        for (old_group, old_key), (
            new_group,
            new_key,
        ) in _LEGACY_ENGINE_CONFIG_KEYS.items():
            with self.subTest(old=(old_group, old_key)):
                self.assertEqual(
                    schema[old_group]["items"][old_key]["default"],
                    schema[new_group]["items"][new_key]["default"],
                )
        self.assertLess(schema["config_version"]["default"], _ENGINE_CONFIG_VERSION)

    def test_schema_defaults_match_shared_constants(self) -> None:
        schema_path = Path(__file__).parents[1] / "_conf_schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        expected = {
            ("ai_behavior", "image_context_isolation"): DEFAULT_IMAGE_CONTEXT_ISOLATION,
            ("ai_behavior", "max_images_per_session"): DEFAULT_MAX_IMAGES_PER_SESSION,
            ("ai_behavior", "image_context_ttl_seconds"): (
                DEFAULT_IMAGE_CONTEXT_TTL_SECONDS
            ),
            ("ai_behavior", "max_image_context_sessions"): (
                DEFAULT_MAX_IMAGE_CONTEXT_SESSIONS
            ),
            ("network", "allow_image_upload"): DEFAULT_ALLOW_IMAGE_UPLOAD,
            ("network", "allow_local_file_access"): DEFAULT_ALLOW_LOCAL_FILE_ACCESS,
            ("saucenao", "similarity_threshold"): DEFAULT_SAUCENAO_SIMILARITY_THRESHOLD,
        }

        self.assertEqual(
            expected,
            {
                (section, key): schema[section]["items"][key]["default"]
                for section, key in expected
            },
        )

    def test_google_lens_schema_matches_runtime_defaults(self) -> None:
        schema_path = Path(__file__).parents[1] / "_conf_schema.json"
        google_lens_items = json.loads(schema_path.read_text(encoding="utf-8"))[
            "google_lens"
        ]["items"]

        self.assertEqual(
            [
                "all",
                "visual_matches",
                "exact_matches",
                "products",
            ],
            google_lens_items["search_type"]["options"],
        )
        self.assertEqual(
            {
                "search_type": DEFAULT_GOOGLE_LENS_SEARCH_TYPE,
                "language": DEFAULT_GOOGLE_LENS_LANGUAGE,
                "country": DEFAULT_GOOGLE_LENS_COUNTRY,
                "safe_search": DEFAULT_GOOGLE_LENS_SAFE_SEARCH,
                "auto_crop": DEFAULT_GOOGLE_LENS_AUTO_CROP,
                "no_cache": DEFAULT_GOOGLE_LENS_NO_CACHE,
            },
            {
                key: google_lens_items[key]["default"]
                for key in (
                    "search_type",
                    "language",
                    "country",
                    "safe_search",
                    "auto_crop",
                    "no_cache",
                )
            },
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

    def test_llm_tool_response_mode_schema_matches_runtime_default(self) -> None:
        schema_path = Path(__file__).parents[1] / "_conf_schema.json"
        ai_behavior_items = json.loads(schema_path.read_text(encoding="utf-8"))[
            "ai_behavior"
        ]["items"]
        plugin = self.make_plugin(SimpleNamespace())
        plugin.config = {}

        mode_schema = ai_behavior_items["llm_tool_response_mode"]
        self.assertEqual(mode_schema["default"], DEFAULT_LLM_TOOL_RESPONSE_MODE)
        self.assertEqual(
            mode_schema["options"],
            ["results_with_summary", "results_only", "llm_only"],
        )
        self.assertEqual(ai_behavior_items["llm_tool_silent_mode"]["default"], "")
        self.assertEqual(
            plugin._get_llm_tool_response_mode(), DEFAULT_LLM_TOOL_RESPONSE_MODE
        )

        plugin.config = {"ai_behavior": {"llm_tool_response_mode": "results_only"}}
        self.assertEqual(plugin._get_llm_tool_response_mode(), "results_only")

        for invalid_mode in ("invalid", [], {}):
            with self.subTest(invalid_mode=invalid_mode):
                plugin.config = {
                    "ai_behavior": {"llm_tool_response_mode": invalid_mode}
                }
                self.assertEqual(
                    plugin._get_llm_tool_response_mode(),
                    DEFAULT_LLM_TOOL_RESPONSE_MODE,
                )

    def test_migrate_legacy_llm_tool_response_config(self) -> None:
        cases = (
            ({"llm_tool_silent_mode": True}, "llm_only"),
            ({"llm_tool_silent_mode": False}, "results_with_summary"),
        )

        for legacy_ai_behavior, expected in cases:
            with self.subTest(legacy_ai_behavior=legacy_ai_behavior):
                config = _SaveableConfig({"ai_behavior": dict(legacy_ai_behavior)})

                ImgExplorationPlugin._migrate_llm_tool_response_config(config)

                ai_behavior = config["ai_behavior"]
                self.assertEqual(ai_behavior["llm_tool_response_mode"], expected)
                self.assertEqual(ai_behavior["llm_tool_silent_mode"], "")
                self.assertFalse(ai_behavior["llm_tool_silent_mode"])
                config.save_config.assert_called_once_with()

    def test_llm_tool_response_config_skips_completed_migration(self) -> None:
        config = _SaveableConfig(
            {
                "ai_behavior": {
                    "llm_tool_response_mode": "results_only",
                    "llm_tool_silent_mode": "",
                }
            }
        )

        ImgExplorationPlugin._migrate_llm_tool_response_config(config)

        self.assertEqual(
            config["ai_behavior"]["llm_tool_response_mode"], "results_only"
        )
        config.save_config.assert_not_called()

    def test_migrate_legacy_engine_config(self) -> None:
        legacy = {
            "api_keys": {
                "saucenao_api_key": "sn_key",
                "serpapi_keys": ["google_key"],
                "ascii2d_session_id": "ascii_sess",
                "ascii2d_cf_clearance": "",
                "trace_moe_api_key": "trace_key",
            },
            "strategies": {
                "enable_saucenao": True,
                "saucenao_similarity_threshold": 65,
                "enable_google_lens": False,
                "google_lens_language": "ja",
                "enable_trace_moe": True,
                "trace_moe_include_in_default": True,
            },
            "display": {"saucenao_max_results": 4, "trace_moe_max_results": 2},
        }
        config = _SaveableConfig(
            {
                **json.loads(json.dumps(legacy)),
                # AstrBot 补入新分组默认值和配置版本后才交给插件
                "saucenao": {"enable": True, "api_key": "", "max_results": 3},
                "google_lens": {"enable": True, "language": "zh-cn"},
                "config_version": 1,
            }
        )

        ImgExplorationPlugin._migrate_engine_config(config)

        self.assertEqual(
            {
                "enable": True,
                "api_key": "sn_key",
                "similarity_threshold": 65,
                "max_results": 4,
            },
            config["saucenao"],
        )
        self.assertEqual(
            {"enable": False, "serpapi_keys": ["google_key"], "language": "ja"},
            config["google_lens"],
        )
        self.assertEqual(
            {"session_id": "ascii_sess", "cf_clearance": ""}, config["ascii2d"]
        )
        self.assertEqual(
            {
                "enable": True,
                "include_in_default": True,
                "api_key": "trace_key",
                "max_results": 2,
            },
            config["trace_moe"],
        )
        # 旧分组保持不变，回退旧版插件时仍可使用
        for group, values in legacy.items():
            self.assertEqual(values, config[group])
        self.assertEqual(_ENGINE_CONFIG_VERSION, config["config_version"])
        config.save_config.assert_called_once_with()

    def test_engine_config_migration_skips_migrated_config(self) -> None:
        config = _SaveableConfig(
            {
                "api_keys": {"saucenao_api_key": "old_key"},
                "saucenao": {"api_key": "new_key"},
                "config_version": _ENGINE_CONFIG_VERSION,
            }
        )

        ImgExplorationPlugin._migrate_engine_config(config)

        self.assertEqual("new_key", config["saucenao"]["api_key"])
        config.save_config.assert_not_called()

    def test_engine_config_migration_tolerates_malformed_groups(self) -> None:
        for config in (
            {},
            {"api_keys": "invalid", "strategies": None, "config_version": "2"},
            {"api_keys": {"saucenao_api_key": "sn_key"}, "saucenao": "invalid"},
        ):
            with self.subTest(config=config):
                ImgExplorationPlugin._migrate_engine_config(config)

                self.assertEqual(_ENGINE_CONFIG_VERSION, config["config_version"])
                if isinstance(config.get("api_keys"), dict):
                    self.assertEqual({"api_key": "sn_key"}, config["saucenao"])

    def test_init_strategies_combinations(self) -> None:
        # 1. All strategies enabled with valid keys/configs
        conf_all = {
            "network": {
                "proxy_url": "http://127.0.0.1:7890",
                "allow_image_upload": False,
                "allow_local_file_access": True,
            },
            "ai_behavior": {
                "image_context_isolation": "global",
                "max_images_per_session": 8,
                "image_context_ttl_seconds": 120,
                "max_image_context_sessions": 16,
            },
            "saucenao": {
                "enable": True,
                "api_key": "sn_key",
                "similarity_threshold": 65,
                "max_results": 4,
            },
            "google_lens": {
                "enable": True,
                "serpapi_keys": ["google_key1"],
                "search_type": "exact_matches",
                "language": "ja",
                "country": "jp",
                "safe_search": False,
                "auto_crop": True,
                "no_cache": True,
                "max_results": 6,
            },
            "ascii2d": {
                "enable": True,
                "session_id": "ascii_sess",
                "cf_clearance": "cf_token",
                "bovw_max_results": 7,
                "color_max_results": 1,
            },
        }

        plugin_all = self.make_plugin(SimpleNamespace())
        plugin_all.config = conf_all
        plugin_all.strategies = []
        with self._patch_strategy_dependencies() as dependencies:
            plugin_all._init_strategies()

        dependencies["set_proxy_url"].assert_called_once_with("http://127.0.0.1:7890")
        dependencies["set_allow_image_upload"].assert_called_once_with(False)
        dependencies["set_allow_local_file_access"].assert_called_once_with(True)
        dependencies["init_image_context_manager"].assert_called_once_with(
            isolation_mode="global",
            max_images=8,
            ttl_seconds=120,
            max_sessions=16,
        )
        dependencies["SauceNaoStrategy"].assert_called_once_with(
            api_key="sn_key",
            similarity_threshold=65,
            max_results=4,
        )
        dependencies["GoogleLensStrategy"].assert_called_once_with(
            api_keys=["google_key1"],
            max_results=6,
            search_type="exact_matches",
            language="ja",
            country="jp",
            safe_search=False,
            auto_crop=True,
            no_cache=True,
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
            "saucenao": {"enable": True},
            "google_lens": {"enable": True},
            "ascii2d": "invalid",
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
            "saucenao": {"enable": False, "api_key": "sn_key"},
            "google_lens": {"enable": False, "serpapi_keys": ["google_key"]},
            "ascii2d": {"enable": False, "session_id": "ascii_sess"},
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
        limit_values = (
            {},
            {
                ("saucenao", "max_results"): 0,
                ("google_lens", "max_results"): "",
                ("ascii2d", "bovw_max_results"): False,
                ("ascii2d", "color_max_results"): -2,
            },
            {
                ("saucenao", "max_results"): None,
                ("google_lens", "max_results"): "invalid",
                ("ascii2d", "bovw_max_results"): [],
                ("ascii2d", "color_max_results"): 0,
            },
        )

        for limits in limit_values:
            with self.subTest(limits=limits):
                plugin = self.make_plugin(SimpleNamespace())
                plugin.config = {
                    "saucenao": {"enable": True, "api_key": "sn_key"},
                    "google_lens": {"enable": True, "serpapi_keys": ["google_key"]},
                    "ascii2d": {"enable": True, "session_id": "ascii_sess"},
                }
                for (group, key), value in limits.items():
                    plugin.config[group][key] = value
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
                    search_type="all",
                    language="zh-cn",
                    country="",
                    safe_search=False,
                    auto_crop=False,
                    no_cache=False,
                )
                dependencies["Ascii2dStrategy"].assert_called_once_with(
                    session_id="ascii_sess",
                    cf_clearance="",
                    bovw_max_results=DEFAULT_ASCII2D_BOVW_MAX_RESULTS,
                    color_max_results=DEFAULT_ASCII2D_COLOR_MAX_RESULTS,
                )

    def test_init_strategies_normalizes_credentials(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        plugin.config = {
            "saucenao": {"enable": True, "api_key": " sn_key\n"},
            "google_lens": {
                "enable": True,
                "serpapi_keys": ["", "  ", " google_key ", None],
            },
            "ascii2d": {
                "enable": True,
                "session_id": " ascii_sess ",
                "cf_clearance": "\tcf_token ",
            },
        }
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
            search_type="all",
            language="zh-cn",
            country="",
            safe_search=False,
            auto_crop=False,
            no_cache=False,
        )
        dependencies["Ascii2dStrategy"].assert_called_once_with(
            session_id="ascii_sess",
            cf_clearance="cf_token",
            bovw_max_results=DEFAULT_ASCII2D_BOVW_MAX_RESULTS,
            color_max_results=DEFAULT_ASCII2D_COLOR_MAX_RESULTS,
        )

        for serpapi_keys in (["", " \n"], "google_key"):
            with self.subTest(serpapi_keys=serpapi_keys):
                blank_plugin = self.make_plugin(SimpleNamespace())
                blank_plugin.config = {
                    "saucenao": {"enable": True, "api_key": "  "},
                    "google_lens": {"enable": True, "serpapi_keys": serpapi_keys},
                    "ascii2d": {"enable": True, "session_id": "\t"},
                }
                blank_plugin.strategies = []
                with self._patch_strategy_dependencies() as dependencies:
                    blank_plugin._init_strategies()

                self.assertEqual(blank_plugin.strategies, [])
                dependencies["SauceNaoStrategy"].assert_not_called()
                dependencies["GoogleLensStrategy"].assert_not_called()
                dependencies["Ascii2dStrategy"].assert_not_called()
