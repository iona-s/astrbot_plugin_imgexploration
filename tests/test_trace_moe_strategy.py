from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from astrbot_plugin_imgexploration.core.models import ProviderSearchError
from astrbot_plugin_imgexploration.core.providers.trace_moe_strategy import (
    TraceMoeStrategy,
)

MODULE = "astrbot_plugin_imgexploration.core.providers.trace_moe_strategy"


def match(**overrides):
    result = {
        "anilist": {
            "id": 123,
            "title": {"native": "テスト", "romaji": "Test"},
            "isAdult": False,
        },
        "filename": "test.mkv",
        "episode": 2,
        "at": 0,
        "from": 0,
        "to": 2,
        "similarity": 0.95,
        "image": "https://api.trace.moe/image/example",
    }
    result.update(overrides)
    return result


class TraceMoeStrategyTests(unittest.IsolatedAsyncioTestCase):
    async def search_payload(self, strategy, payload, status=200):
        response = AsyncMock()
        response.status = status
        response.json.return_value = payload
        context = AsyncMock()
        context.__aenter__.return_value = response
        session = MagicMock()
        session.get.return_value = context
        with patch(f"{MODULE}.get_aiohttp_session", return_value=session):
            result = await strategy.search("https://example.com/image?a=1&b=2")
        return result, session

    async def test_request_and_result_fields(self):
        with patch(f"{MODULE}.get_proxy_url", return_value="http://proxy:7890"):
            outcome, session = await self.search_payload(
                TraceMoeStrategy(api_key=" test-token "),
                {"error": "", "result": [match()]},
            )
        request = session.get.call_args.kwargs
        self.assertEqual(
            request["params"],
            {
                "url": "https://example.com/image?a=1&b=2",
                "anilistInfo": "1",
                "cutBorders": "1",
            },
        )
        self.assertEqual(request["headers"]["x-trace-key"], "test-token")
        self.assertEqual(request["proxy"], "http://proxy:7890")
        item = outcome.items[0]
        self.assertEqual(item.title, "テスト")
        self.assertEqual(item.url, "https://anilist.co/anime/123")
        self.assertEqual(item.similarity, "95.00%")
        self.assertIn("第2集", item.description)
        self.assertIn("文件内时间点: 00:00", item.description)

    async def test_guest_request_without_cropping(self):
        outcome, session = await self.search_payload(
            TraceMoeStrategy(cut_borders=False), {"result": []}
        )
        self.assertEqual(outcome.items, [])
        self.assertEqual(outcome.user_notices, [])
        self.assertNotIn("cutBorders", session.get.call_args.kwargs["params"])
        self.assertNotIn("x-trace-key", session.get.call_args.kwargs["headers"])

    async def test_threshold_sort_limit_and_low_confidence(self):
        nodes = [match(similarity=0.2), match(similarity=0.92), match(similarity=0.98)]
        result, _ = await self.search_payload(
            TraceMoeStrategy(max_results=1), {"result": nodes}
        )
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].similarity, "98.00%")
        result, _ = await self.search_payload(
            TraceMoeStrategy(), {"result": [nodes[0]]}
        )
        self.assertFalse(result.items)
        self.assertTrue(result.user_notices)
        result, _ = await self.search_payload(
            TraceMoeStrategy(similarity_threshold=10), {"result": [nodes[0]]}
        )
        self.assertIn("低置信度", result.items[0].description)

    async def test_invalid_threshold_uses_default(self):
        nodes = [match(similarity=0.85), match(similarity=0.95)]
        for threshold in ("invalid", None):
            with self.subTest(threshold=threshold):
                result, _ = await self.search_payload(
                    TraceMoeStrategy(similarity_threshold=threshold),
                    {"result": nodes},
                )
                self.assertEqual([item.similarity for item in result.items], ["95.00%"])

    async def test_missing_fields_adult_range_and_long_time(self):
        nodes = [
            match(
                anilist={"id": 123, "isAdult": True},
                episode_start=1,
                episode_end=2,
                at=3661,
            ),
            match(anilist=456, episode=None, at=None, **{"from": None, "to": None}),
            match(anilist=None, filename=None, episode="OVA", at=None, **{"from": 0}),
        ]
        result, _ = await self.search_payload(TraceMoeStrategy(), {"result": nodes})
        self.assertEqual(result.items[0].title, "test.mkv")
        self.assertFalse(result.items[0].thumbnail)
        self.assertIn("第1–2集", result.items[0].description)
        self.assertIn("01:01:01", result.items[0].description)
        self.assertEqual(result.items[1].url, "https://anilist.co/anime/456")
        self.assertIn("集数未知", result.items[1].description)
        self.assertIn("时间点未知", result.items[1].description)
        self.assertEqual(result.items[2].url, "")
        self.assertEqual(result.items[2].title, "未知番名")
        self.assertIn("OVA", result.items[2].description)
        self.assertIn("00:00", result.items[2].description)

    async def test_episode_arrays(self):
        cases = [
            ([3], "第3集"),
            ([1, 2], "第1–2集"),
            ([], "集数未知"),
            ([2, 1], "集数未知"),
            ([1, "OVA"], "集数未知"),
            ([0, 1], "集数未知"),
        ]
        for episode, expected in cases:
            with self.subTest(episode=episode):
                result, _ = await self.search_payload(
                    TraceMoeStrategy(), {"result": [match(episode=episode)]}
                )
                self.assertIn(expected, result.items[0].description)

    async def test_invalid_candidates_are_ignored(self):
        nodes = [
            None,
            match(similarity=float("nan")),
            match(similarity=True),
            match(similarity=2),
            match(similarity="0.95"),
        ]
        result, _ = await self.search_payload(TraceMoeStrategy(), {"result": nodes})
        self.assertFalse(result.items)
        self.assertTrue(result.user_notices)

    async def test_http_and_api_failures(self):
        for status in (400, 402, 403, 429, 503, 504):
            with (
                self.subTest(status=status),
                self.assertRaises(ProviderSearchError) as error,
            ):
                await self.search_payload(TraceMoeStrategy(), {}, status)
            self.assertEqual(
                error.exception.user_notice is not None, status in (402, 429)
            )
        for payload in ([], {"error": "bad secret"}, {"result": None}):
            with self.subTest(payload=payload), self.assertRaises(ProviderSearchError):
                await self.search_payload(TraceMoeStrategy(), payload)
        with (
            patch(
                f"{MODULE}.get_aiohttp_session", side_effect=ValueError("sensitive-url")
            ),
            patch(f"{MODULE}.logger.warning") as log,
        ):
            with self.assertRaises(ProviderSearchError) as error:
                await TraceMoeStrategy().search("https://example.com/image")
            self.assertNotIn("sensitive-url", str(error.exception))
            self.assertNotIn("sensitive-url", str(log.call_args))

    async def test_invalid_input_never_requests_network(self):
        for url in (
            "base64://abc",
            "file:///image",
            "https:///missing-host",
            "http://[",
        ):
            with (
                self.subTest(url=url),
                patch(f"{MODULE}.get_aiohttp_session") as get_session,
            ):
                with self.assertRaises(ProviderSearchError):
                    await TraceMoeStrategy().search(url)
                get_session.assert_not_called()

    async def test_searches_are_serial_and_cancel_releases_slot(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        active = 0
        maximum = 0
        calls = 0

        class ResponseContext:
            async def __aenter__(self):
                nonlocal active, maximum, calls
                active += 1
                calls += 1
                maximum = max(maximum, active)
                entered.set()
                return self

            async def __aexit__(self, *_):
                nonlocal active
                active -= 1

            status = 200

            async def json(self):
                await release.wait()
                return {"result": []}

        session = MagicMock()
        session.get.side_effect = lambda *_, **__: ResponseContext()
        strategy = TraceMoeStrategy()
        with patch(f"{MODULE}.get_aiohttp_session", return_value=session):
            first = asyncio.create_task(strategy.search("https://example.com/one"))
            await entered.wait()
            second = asyncio.create_task(strategy.search("https://example.com/two"))
            await asyncio.sleep(0)
            self.assertEqual(calls, 1)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            release.set()
            await second
        self.assertEqual(calls, 2)
        self.assertEqual(maximum, 1)
