"""图片来源解析与回复回退测试"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot.core.message.components import Image, Reply
from astrbot_plugin_imgexploration.core.image_sources import (
    as_http_image_url,
    get_image_from_reply,
    get_raw_image_urls,
    partition_image_sources,
)


class HttpImageUrlTests(unittest.TestCase):
    def test_accepts_http_urls_without_rewriting_them(self) -> None:
        for value in (
            "http://image.example/source.jpg",
            "HTTPS://image.example/source.jpg",
        ):
            with self.subTest(value=value):
                self.assertEqual(as_http_image_url(value), value)

    def test_rejects_non_http_and_non_string_values(self) -> None:
        for value in (None, 123, "", "file:///tmp/source.jpg", "base64://image"):
            with self.subTest(value=value):
                self.assertIsNone(as_http_image_url(value))


class PartitionImageSourcesTests(unittest.TestCase):
    def test_preserves_group_order_and_deduplicates_fields(self) -> None:
        file_url = "https://image.example/from-file.jpg"
        raw_url = "https://image.example/raw.jpg"
        component_url = "https://image.example/from-url.jpg"
        local_url = "file:///tmp/source.jpg"

        http_sources, other_sources = partition_image_sources(
            Image(file=file_url, url=local_url),
            raw_url,
            Image(file=file_url, url=component_url),
            None,
            "",
        )

        self.assertEqual(http_sources, [file_url, raw_url, component_url])
        self.assertEqual(other_sources, [local_url])


class RawImageExtractionTests(unittest.TestCase):
    def test_ignores_absent_and_malformed_shapes(self) -> None:
        for raw_message in (
            None,
            {},
            {"message": "not-a-message-chain"},
        ):
            with self.subTest(raw_message=raw_message):
                event = SimpleNamespace(
                    message_obj=SimpleNamespace(raw_message=raw_message)
                )
                self.assertEqual(get_raw_image_urls(event), [])

        event = SimpleNamespace(
            message_obj=SimpleNamespace(
                raw_message=SimpleNamespace(
                    message=[
                        None,
                        {"type": "text", "data": {"url": "https://example/text"}},
                        {"type": "image", "data": None},
                        {"type": "image", "data": {"url": 123}},
                        {
                            "type": "image",
                            "data": {"url": "file:///tmp/image.jpg"},
                        },
                    ]
                )
            )
        )
        self.assertEqual(get_raw_image_urls(event), [])

    def test_extracts_http_urls_from_mapping_and_object_shapes(self) -> None:
        first_url = "https://image.example/first.jpg"
        second_url = "http://image.example/second.jpg"

        for raw_message in (
            {
                "message": [
                    {"type": "image", "data": {"url": first_url}},
                    {"type": "image", "data": {"url": second_url}},
                ]
            },
            SimpleNamespace(
                message=(
                    {"type": "image", "data": {"url": first_url}},
                    {"type": "image", "data": {"url": second_url}},
                )
            ),
        ):
            with self.subTest(raw_message=raw_message):
                event = SimpleNamespace(
                    message_obj=SimpleNamespace(raw_message=raw_message)
                )
                self.assertEqual(get_raw_image_urls(event), [first_url, second_url])


class ReplyImageTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_bot(
        *,
        url: str | None = None,
        file: str | None = None,
    ) -> SimpleNamespace:
        data = {}
        if url is not None:
            data["url"] = url
        if file is not None:
            data["file"] = file
        return SimpleNamespace(
            call_action=AsyncMock(
                return_value={
                    "message": [
                        {
                            "type": "image",
                            "data": data,
                        }
                    ]
                }
            )
        )

    async def test_http_chain_is_used_before_onebot_fallback(self) -> None:
        event = SimpleNamespace()
        reply = Reply(
            id="123",
            chain=[Image(file="https://image.example/reply-chain.jpg")],
        )

        with patch(
            "astrbot_plugin_imgexploration.core.image_sources.get_bot_api"
        ) as get_bot_api:
            image = await get_image_from_reply(event, reply)

        self.assertIs(image, reply.chain[0])
        get_bot_api.assert_not_called()

    async def test_local_chain_falls_back_to_onebot_http(self) -> None:
        event = SimpleNamespace()
        reply = Reply(
            id="123",
            chain=[
                Image(
                    file="local-file-token",
                    url="file:///tmp/reply-image.jpg",
                )
            ],
        )
        bot = self.make_bot(
            url="https://image.example/onebot.jpg",
            file="onebot-file-token",
        )

        with patch(
            "astrbot_plugin_imgexploration.core.image_sources.get_bot_api",
            return_value=bot,
        ):
            image = await get_image_from_reply(event, reply)

        self.assertIsInstance(image, Image)
        assert image is not None
        self.assertEqual(image.url, "https://image.example/onebot.jpg")
        self.assertEqual(image.file, "onebot-file-token")
        bot.call_action.assert_awaited_once_with("get_msg", message_id=123)

    async def test_local_chain_beats_onebot_file_token(self) -> None:
        event = SimpleNamespace()
        reply = Reply(
            id="123",
            chain=[Image(file="file:///tmp/reply-image.jpg")],
        )
        bot = self.make_bot(file="onebot-file-token")

        with patch(
            "astrbot_plugin_imgexploration.core.image_sources.get_bot_api",
            return_value=bot,
        ):
            image = await get_image_from_reply(event, reply)

        self.assertIs(image, reply.chain[0])
        bot.call_action.assert_awaited_once_with("get_msg", message_id=123)

    async def test_local_chain_remains_final_fallback(self) -> None:
        event = SimpleNamespace()
        reply = Reply(
            id="123",
            chain=[Image(file="file:///tmp/reply-image.jpg")],
        )

        with patch(
            "astrbot_plugin_imgexploration.core.image_sources.get_bot_api",
            return_value=None,
        ):
            image = await get_image_from_reply(event, reply)

        self.assertIs(image, reply.chain[0])

    async def test_empty_chain_falls_back_to_onebot_get_msg(self) -> None:
        event = SimpleNamespace()
        reply = Reply(id="123", chain=[])
        bot = self.make_bot(
            url="https://image.example/onebot.jpg",
            file="onebot-file-token",
        )

        with patch(
            "astrbot_plugin_imgexploration.core.image_sources.get_bot_api",
            return_value=bot,
        ):
            image = await get_image_from_reply(event, reply)

        self.assertIsInstance(image, Image)
        assert image is not None
        self.assertEqual(image.url, "https://image.example/onebot.jpg")
        self.assertEqual(image.file, "onebot-file-token")
        bot.call_action.assert_awaited_once_with("get_msg", message_id=123)
