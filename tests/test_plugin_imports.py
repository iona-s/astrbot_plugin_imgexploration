"""Plugin import smoke checks for the final core layout."""

from __future__ import annotations

import importlib
import unittest

_EXPECTED_CORE_MODULES = (
    "astrbot_plugin_imgexploration.core.constant",
    "astrbot_plugin_imgexploration.core.image_context",
    "astrbot_plugin_imgexploration.core.image_sources",
    "astrbot_plugin_imgexploration.core.image_wait",
    "astrbot_plugin_imgexploration.core.models",
    "astrbot_plugin_imgexploration.core.result_sender",
    "astrbot_plugin_imgexploration.core.service",
    "astrbot_plugin_imgexploration.core.strategy",
    "astrbot_plugin_imgexploration.core.utils",
    "astrbot_plugin_imgexploration.core.providers.ascii2d_strategy",
    "astrbot_plugin_imgexploration.core.providers.google_lens_strategy",
    "astrbot_plugin_imgexploration.core.providers.sauce_nao_strategy",
)


class PluginImportSmokeTests(unittest.TestCase):
    def test_root_entry_point_importable(self) -> None:
        module = importlib.import_module("astrbot_plugin_imgexploration.main")
        self.assertTrue(hasattr(module, "ImgExplorationPlugin"))
        plugin_cls = module.ImgExplorationPlugin
        for handler_name in ("on_message", "search_image_cmd", "tool_search_image"):
            self.assertTrue(
                hasattr(plugin_cls, handler_name),
                f"missing handler: {handler_name}",
            )

    def test_core_modules_importable(self) -> None:
        for module_name in _EXPECTED_CORE_MODULES:
            with self.subTest(module=module_name):
                importlib.import_module(module_name)
