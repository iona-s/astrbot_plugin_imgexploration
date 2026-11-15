from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from astrbot.core.message.components import Image

from .helpers import FakeEvent, PluginTestCase, use_isolated_astrbot_temp_dir


class ImageCaptureTests(PluginTestCase):
    async def test_captures_astrbot_temp_images_only_when_allowed(self) -> None:
        image_path = use_isolated_astrbot_temp_dir(self) / "media_image_capture.jpg"
        image_path.write_bytes(b"image")
        plugin = self.make_plugin(SimpleNamespace())

        for allowed in (True, False):
            with self.subTest(allowed=allowed):
                image_context = SimpleNamespace(
                    add_image=Mock(), current_sequence=Mock(return_value=0)
                )
                # AstrBot 4.26 起非 OneBot 平台的图片组件已改写为本地临时路径
                event = FakeEvent(
                    [],
                    message_str="",
                    messages=[Image(file=str(image_path), url=str(image_path))],
                    is_command=False,
                )
                with (
                    patch(
                        "astrbot_plugin_imgexploration.main.get_image_context_manager",
                        return_value=image_context,
                    ),
                    patch(
                        "astrbot_plugin_imgexploration.main.is_astrbot_temp_images_allowed",
                        return_value=allowed,
                    ),
                ):
                    await plugin.on_message(event)

                self.assertEqual(
                    image_context.add_image.call_args_list,
                    [
                        call(
                            event,
                            str(image_path),
                            message_id="message-1",
                            sender_id="user-1",
                            is_sticker=False,
                        )
                    ]
                    if allowed
                    else [],
                )

    async def test_captures_each_image_by_its_own_source(self) -> None:
        image_path = use_isolated_astrbot_temp_dir(self) / "media_image_mixed.jpg"
        image_path.write_bytes(b"image")
        plugin = self.make_plugin(SimpleNamespace())
        image_context = SimpleNamespace(
            add_image=Mock(), current_sequence=Mock(return_value=0)
        )
        failed_url = "https://image.example/not-localized.jpg"
        # 非 OneBot 平台：一张图片已本地化，另一张下载失败仍保留 HTTP 地址
        event = FakeEvent(
            [],
            message_str="",
            messages=[
                Image(file=str(image_path), url=str(image_path)),
                Image(file=failed_url, url=failed_url),
            ],
            is_command=False,
        )

        with (
            patch(
                "astrbot_plugin_imgexploration.main.get_image_context_manager",
                return_value=image_context,
            ),
            patch(
                "astrbot_plugin_imgexploration.main.is_astrbot_temp_images_allowed",
                return_value=True,
            ),
        ):
            await plugin.on_message(event)

        # 按消息内顺序捕获，image_index=-1 仍对应消息中的最后一张
        self.assertEqual(
            [call_args.args[1] for call_args in image_context.add_image.call_args_list],
            [str(image_path), failed_url],
        )

    async def test_prefers_raw_http_over_astrbot_temp_image(self) -> None:
        image_path = use_isolated_astrbot_temp_dir(self) / "media_image_onebot.jpg"
        image_path.write_bytes(b"image")
        plugin = self.make_plugin(SimpleNamespace())
        image_context = SimpleNamespace(
            add_image=Mock(), current_sequence=Mock(return_value=0)
        )
        raw_url = "https://image.example/onebot.jpg"
        # OneBot 图片也会被 AstrBot 本地化，但原始事件仍保留 HTTP 地址
        event = FakeEvent(
            [],
            message_str="",
            messages=[Image(file=str(image_path), url=str(image_path))],
            is_command=False,
            raw_message={"message": [{"type": "image", "data": {"url": raw_url}}]},
        )

        with (
            patch(
                "astrbot_plugin_imgexploration.main.get_image_context_manager",
                return_value=image_context,
            ),
            patch(
                "astrbot_plugin_imgexploration.main.is_astrbot_temp_images_allowed",
                return_value=True,
            ),
        ):
            await plugin.on_message(event)

        image_context.add_image.assert_called_once_with(
            event,
            raw_url,
            message_id="message-1",
            sender_id="user-1",
            is_sticker=False,
        )

    async def test_uses_raw_http_when_component_is_local(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        image_context = SimpleNamespace(
            add_image=Mock(), current_sequence=Mock(return_value=0)
        )
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
            is_sticker=False,
        )

    async def test_uses_raw_http_without_matching_component(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        image_context = SimpleNamespace(
            add_image=Mock(), current_sequence=Mock(return_value=0)
        )
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
            is_sticker=False,
        )

    async def test_checks_component_url_and_file_independently(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        image_context = SimpleNamespace(
            add_image=Mock(), current_sequence=Mock(return_value=0)
        )
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
            is_sticker=False,
        )

    async def test_preserves_candidate_order_and_deduplicates(self) -> None:
        plugin = self.make_plugin(SimpleNamespace())
        image_context = SimpleNamespace(
            add_image=Mock(), current_sequence=Mock(return_value=0)
        )
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
                    is_sticker=False,
                ),
                call(
                    event,
                    component_file,
                    message_id="message-1",
                    sender_id="user-1",
                    is_sticker=False,
                ),
                call(
                    event,
                    raw_url,
                    message_id="message-1",
                    sender_id="user-1",
                    is_sticker=False,
                ),
            ],
        )
