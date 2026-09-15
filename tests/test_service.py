from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from astrbot_plugin_imgexploration.core.models import (
    ExplorationResult,
    ProviderSearchError,
    ProviderSearchOutcome,
    SearchResultItem,
)
from astrbot_plugin_imgexploration.core.service import ImgExplorationService
from astrbot_plugin_imgexploration.core.strategy import ImageSearchStrategy


class DummyStrategy(ImageSearchStrategy):
    def __init__(
        self,
        name: str,
        items: list[SearchResultItem] | ProviderSearchOutcome | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self.name = name
        self.items = items if items is not None else []
        self.raise_exc = raise_exc

    def get_service_name(self) -> str:
        return self.name

    async def search(
        self, image_url: str
    ) -> list[SearchResultItem] | ProviderSearchOutcome:
        if self.raise_exc:
            raise self.raise_exc
        return self.items


class SearchResultItemTests(unittest.TestCase):
    def test_with_thumbnail_bytes_returns_new_item(self) -> None:
        original = SearchResultItem(
            title="Result",
            url="https://source.example/result",
            thumbnail="https://source.example/thumb.jpg",
            source="Provider",
            similarity="91%",
            description="Description",
            domain="source.example",
        )

        updated = original.with_thumbnail_bytes(b"thumbnail")

        self.assertIsNot(updated, original)
        self.assertIsNone(original.thumbnail_bytes)
        self.assertEqual(b"thumbnail", updated.thumbnail_bytes)
        self.assertEqual(original.title, updated.title)
        self.assertEqual(original.url, updated.url)
        self.assertEqual(original.thumbnail, updated.thumbnail)
        self.assertEqual(original.source, updated.source)
        self.assertEqual(original.similarity, updated.similarity)
        self.assertEqual(original.description, updated.description)
        self.assertEqual(original.domain, updated.domain)


class ImgExplorationServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_available_strategy_names_preserve_registration_order(self) -> None:
        strat_a = DummyStrategy("SauceNAO")
        strat_b = DummyStrategy("Ascii2D")
        service = ImgExplorationService([strat_a, strat_b])

        self.assertEqual(service.get_available_strategies(), ["SauceNAO", "Ascii2D"])

    def test_resolve_strategy_names(self) -> None:
        strat_sauce = DummyStrategy("SauceNAO")
        strat_ascii = DummyStrategy("Ascii2d")
        strat_lens = DummyStrategy("Google Lens")
        service = ImgExplorationService([strat_sauce, strat_ascii, strat_lens])

        # None or empty returns all
        resolved_all, not_found_all = service.resolve_strategy_names(None)
        self.assertEqual(resolved_all, [strat_sauce, strat_ascii, strat_lens])
        self.assertEqual(not_found_all, [])

        # Alias resolution, case insensitivity, and deduplication
        resolved, not_found = service.resolve_strategy_names(
            ["sauce", "ascii", "google", "sauce", "unknown_strat"]
        )
        self.assertEqual(resolved, [strat_sauce, strat_ascii, strat_lens])
        self.assertEqual(not_found, ["unknown_strat"])

    async def test_explore_with_invalid_or_missing_strategies(self) -> None:
        strat_a = DummyStrategy("SauceNAO")
        service = ImgExplorationService([strat_a])

        # Specified strategy names all not found
        res1 = await service.explore(
            "https://example.com/target.jpg", ["unknown_strategy"]
        )
        self.assertIsInstance(res1, ExplorationResult)
        self.assertEqual(len(res1.items), 0)
        self.assertEqual(res1.attempted_providers, [])
        self.assertEqual(res1.failed_providers, [])
        self.assertFalse(res1.all_failed)

        # No strategies configured in service
        empty_service = ImgExplorationService([])
        res2 = await empty_service.explore("https://example.com/target.jpg")
        self.assertIsInstance(res2, ExplorationResult)
        self.assertEqual(len(res2.items), 0)
        self.assertEqual(res2.attempted_providers, [])
        self.assertEqual(res2.failed_providers, [])
        self.assertFalse(res2.all_failed)

    async def test_explore_parallel_execution_and_exception_handling(self) -> None:
        item_a = SearchResultItem(title="Result A", url="https://source.a/1")
        item_b = SearchResultItem(title="Result B", url="https://source.b/1")

        strat_success1 = DummyStrategy("StratSuccess1", [item_a])
        strat_failing = DummyStrategy(
            "StratFailing", raise_exc=ProviderSearchError("Provider offline")
        )
        strat_success2 = DummyStrategy("StratSuccess2", [item_b])

        service = ImgExplorationService([strat_success1, strat_failing, strat_success2])

        with patch.object(service, "_fill_thumbnails", new=AsyncMock()) as mock_fill:
            result = await service.explore("https://example.com/image.jpg")

            self.assertEqual(len(result.items), 2)
            self.assertEqual(result.items[0].title, "Result A")
            self.assertEqual(result.items[1].title, "Result B")
            self.assertEqual(
                result.attempted_providers,
                ["StratSuccess1", "StratFailing", "StratSuccess2"],
            )
            self.assertEqual(result.failed_providers, ["StratFailing"])
            self.assertFalse(result.all_failed)
            mock_fill.assert_awaited_once_with([item_a, item_b])

    async def test_explore_main_flow_exception_handling(self) -> None:
        strat = DummyStrategy("SauceNAO")
        service = ImgExplorationService([strat])

        with patch.object(
            service, "_fill_thumbnails", side_effect=Exception("Unexpected crash")
        ):
            result = await service.explore("https://example.com/target.jpg")
            self.assertIsInstance(result, ExplorationResult)
            self.assertEqual(len(result.items), 0)
            self.assertEqual(result.attempted_providers, ["SauceNAO"])
            self.assertEqual(result.failed_providers, ["SauceNAO"])
            self.assertTrue(result.all_failed)

    async def test_explore_all_providers_failed(self) -> None:
        strat_fail1 = DummyStrategy(
            "Fail1", raise_exc=ProviderSearchError("Auth failed")
        )
        strat_fail2 = DummyStrategy("Fail2", raise_exc=ProviderSearchError("Timeout"))

        service = ImgExplorationService([strat_fail1, strat_fail2])

        with patch.object(service, "_fill_thumbnails", new=AsyncMock()):
            result = await service.explore("https://example.com/image.jpg")

            self.assertEqual(len(result.items), 0)
            self.assertEqual(result.attempted_providers, ["Fail1", "Fail2"])
            self.assertEqual(result.failed_providers, ["Fail1", "Fail2"])
            self.assertTrue(result.all_failed)

    async def test_explore_valid_empty_result_is_not_failure(self) -> None:
        strat_empty = DummyStrategy("EmptyProvider", [])
        service = ImgExplorationService([strat_empty])

        with patch.object(service, "_fill_thumbnails", new=AsyncMock()):
            result = await service.explore("https://example.com/image.jpg")

            self.assertEqual(len(result.items), 0)
            self.assertEqual(result.attempted_providers, ["EmptyProvider"])
            self.assertEqual(result.failed_providers, [])
            self.assertFalse(result.all_failed)

    async def test_explore_preserves_provider_notices(self) -> None:
        notice = "[SauceNAO]返回结果均低于40%相似度阈值"
        item = SearchResultItem(
            title="Lens Result",
            url="https://source.example/lens",
            source="Google Lens",
        )
        strat_notice = DummyStrategy(
            "SauceNAO",
            ProviderSearchOutcome(user_notices=[notice]),
        )
        strat_success = DummyStrategy("Google Lens", [item])
        service = ImgExplorationService([strat_notice, strat_success])

        with patch.object(service, "_fill_thumbnails", new=AsyncMock()):
            result = await service.explore("https://example.com/image.jpg")

        self.assertEqual(result.items, [item])
        self.assertEqual(result.user_notices, [notice])
        self.assertEqual(result.failed_providers, [])
        self.assertFalse(result.all_failed)

    async def test_explore_partial_success_with_empty_and_failure(self) -> None:
        item = SearchResultItem(title="Result", url="https://source.com/1")
        strat_success = DummyStrategy("Success", [item])
        strat_empty = DummyStrategy("Empty", [])
        strat_fail = DummyStrategy("Fail", raise_exc=ProviderSearchError("Error"))

        service = ImgExplorationService([strat_success, strat_empty, strat_fail])

        with patch.object(service, "_fill_thumbnails", new=AsyncMock()):
            result = await service.explore("https://example.com/image.jpg")

            self.assertEqual(len(result.items), 1)
            self.assertEqual(result.items[0].title, "Result")
            self.assertEqual(result.attempted_providers, ["Success", "Empty", "Fail"])
            self.assertEqual(result.failed_providers, ["Fail"])
            self.assertFalse(result.all_failed)

    async def test_fill_thumbnails(self) -> None:
        # Item 1: Already has thumbnail_bytes -> skip
        item1 = SearchResultItem(
            title="1",
            url="http://1",
            thumbnail="http://thumb/1",
            thumbnail_bytes=b"existing",
        )
        # Item 2: No thumbnail URL -> skip
        item2 = SearchResultItem(title="2", url="http://2", thumbnail=None)
        # Item 3: Has thumbnail URL, download succeeds -> updated
        item3 = SearchResultItem(title="3", url="http://3", thumbnail="http://thumb/3")
        # Item 4: Has thumbnail URL, download returns None -> not updated
        item4 = SearchResultItem(title="4", url="http://4", thumbnail="http://thumb/4")

        items = [item1, item2, item3, item4]

        async def mock_download_bytes(url: str) -> bytes | None:
            if url == "http://thumb/3":
                return b"downloaded_bytes_3"
            return None

        with patch(
            "astrbot_plugin_imgexploration.core.service.download_bytes",
            side_effect=mock_download_bytes,
        ):
            await ImgExplorationService._fill_thumbnails(items)

            self.assertEqual(items[0].thumbnail_bytes, b"existing")
            self.assertIsNone(items[1].thumbnail_bytes)
            self.assertIsNot(items[2], item3)
            self.assertIsNone(item3.thumbnail_bytes)
            self.assertEqual(items[2].thumbnail_bytes, b"downloaded_bytes_3")
            self.assertIsNone(items[3].thumbnail_bytes)
