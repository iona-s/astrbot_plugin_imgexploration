from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from astrbot.core.message.components import Image

from .helpers import FakeEvent, PluginTestCase


class ImageCaptureTests(PluginTestCase):
    async def test_uses_raw_http_when_component_is_local(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        image_context = SimpleNamespace(add_image=Mock())
        raw_url = "https://image.example/source.jpg?secret=signed-value"
        event = FakeEvent(
            [],
            message_str="",
            messages=[
                Image(
                    file="local-file-token",
                    url="file:///tmp/local-image.jpg",
                )
            ],
            is_command=False,
            raw_message={
                "message": [
                    {
                        "type": "image",
                        "data": {"url": raw_url},
                    }
                ]
            },
        )

        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager",
            return_value=image_context,
        ):
            await plugin.on_message(event)

        image_context.add_image.assert_called_once_with(
            event,
            raw_url,
            message_id="message-1",
            sender_id="user-1",
        )

    async def test_uses_raw_http_without_matching_component(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        image_context = SimpleNamespace(add_image=Mock())
        raw_url = "https://image.example/source.jpg?secret=signed-value"
        event = FakeEvent(
            [],
            message_str="",
            messages=[
                SimpleNamespace(
                    file="local-file-token",
                    url=raw_url,
                )
            ],
            is_command=False,
            raw_message=SimpleNamespace(
                message=[
                    {
                        "type": "image",
                        "data": {"url": raw_url},
                    }
                ]
            ),
        )

        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager",
            return_value=image_context,
        ):
            await plugin.on_message(event)

        image_context.add_image.assert_called_once_with(
            event,
            raw_url,
            message_id="message-1",
            sender_id="user-1",
        )

    async def test_checks_component_url_and_file_independently(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        image_context = SimpleNamespace(add_image=Mock())
        file_url = "https://image.example/from-file.jpg"
        event = FakeEvent(
            [],
            message_str="",
            messages=[
                Image(
                    file=file_url,
                    url="file:///tmp/local-image.jpg",
                )
            ],
            is_command=False,
        )

        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager",
            return_value=image_context,
        ):
            await plugin.on_message(event)

        image_context.add_image.assert_called_once_with(
            event,
            file_url,
            message_id="message-1",
            sender_id="user-1",
        )

    async def test_preserves_candidate_order_and_deduplicates(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        image_context = SimpleNamespace(add_image=Mock())
        component_url = "https://image.example/component-url.jpg"
        component_file = "https://image.example/component-file.jpg"
        raw_url = "https://image.example/raw-fallback.jpg"
        event = FakeEvent(
            [],
            message_str="",
            messages=[
                Image(
                    file=component_file,
                    url=component_url,
                ),
                Image(
                    file="local-file-token",
                    url="file:///tmp/local-image.jpg",
                ),
            ],
            is_command=False,
            raw_message={
                "message": [
                    {
                        "type": "image",
                        "data": {"url": component_url},
                    },
                    {
                        "type": "image",
                        "data": {"url": raw_url},
                    },
                    {
                        "type": "image",
                        "data": {"url": component_file},
                    },
                ]
            },
        )

        with patch(
            "astrbot_plugin_imgexploration.main.get_image_context_manager",
            return_value=image_context,
        ):
            await plugin.on_message(event)

        self.assertEqual(
            image_context.add_image.call_args_list,
            [
                call(
                    event,
                    component_url,
                    message_id="message-1",
                    sender_id="user-1",
                ),
                call(
                    event,
                    component_file,
                    message_id="message-1",
                    sender_id="user-1",
                ),
                call(
                    event,
                    raw_url,
                    message_id="message-1",
                    sender_id="user-1",
                ),
            ],
        )


class RawImageExtractionTests(PluginTestCase):
    def test_ignores_absent_and_malformed_shapes(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())

        for raw_message in (
            None,
            {},
            {"message": "not-a-message-chain"},
        ):
            with self.subTest(raw_message=raw_message):
                event = FakeEvent([], raw_message=raw_message)
                self.assertEqual(plugin._get_raw_image_urls(event), [])

        event = FakeEvent(
            [],
            raw_message=SimpleNamespace(
                message=[
                    None,
                    {"type": "text", "data": {"url": "https://example/text"}},
                    {"type": "image", "data": None},
                    {"type": "image", "data": {"url": 123}},
                    {"type": "image", "data": {"url": "file:///tmp/image.jpg"}},
                ]
            ),
        )
        self.assertEqual(plugin._get_raw_image_urls(event), [])
