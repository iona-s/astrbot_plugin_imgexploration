from __future__ import annotations

import base64
import io
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from astrbot_plugin_imgexploration.core.utils import (
    DownloadTooLargeError,
    _read_file_bytes,
    _sanitize_url_for_logging,
    close_aiohttp_session,
    download_bytes,
    get_aiohttp_session,
    get_bot_api,
    get_proxy_url,
    get_search_image,
    is_aiocqhttp_platform,
    is_astrbot_temp_images_allowed,
    is_image_upload_allowed,
    is_local_file_access_allowed,
    read_image_bytes,
    set_allow_astrbot_temp_images,
    set_allow_image_upload,
    set_allow_local_file_access,
    set_proxy_url,
    upload_image,
)

from .helpers import use_isolated_astrbot_temp_dir


class UtilsGlobalConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.orig_proxy = get_proxy_url()
        self.orig_upload = is_image_upload_allowed()
        self.orig_local = is_local_file_access_allowed()

    def tearDown(self) -> None:
        set_proxy_url(self.orig_proxy)
        set_allow_image_upload(self.orig_upload)
        set_allow_local_file_access(self.orig_local)

    def test_sanitize_url_for_logging(self) -> None:
        self.assertEqual(_sanitize_url_for_logging(""), "")
        self.assertEqual(
            _sanitize_url_for_logging("https://example.com/pic.jpg"),
            "https://example.com/pic.jpg",
        )

        url_sensitive = (
            "https://example.com/search?key=secret123&api_key=xyz&q=anime#top"
        )
        sanitized = _sanitize_url_for_logging(url_sensitive)
        self.assertIn("key=***REDACTED***", sanitized)
        self.assertIn("api_key=***REDACTED***", sanitized)
        self.assertIn("q=anime", sanitized)
        self.assertTrue(sanitized.endswith("#top"))

    def test_proxy_url_getter_setter(self) -> None:
        set_proxy_url("http://127.0.0.1:7890")
        self.assertEqual(get_proxy_url(), "http://127.0.0.1:7890")

        set_proxy_url("invalid_proxy")
        self.assertIsNone(get_proxy_url())

        set_proxy_url("")
        self.assertIsNone(get_proxy_url())


class UtilsAsyncSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_aiohttp_session_lifecycle(self) -> None:
        await close_aiohttp_session()
        session1 = await get_aiohttp_session()
        self.assertFalse(session1.closed)

        session2 = await get_aiohttp_session()
        self.assertIs(session1, session2)

        await close_aiohttp_session()
        self.assertTrue(session1.closed)

        session3 = await get_aiohttp_session()
        self.assertIsNot(session1, session3)
        await close_aiohttp_session()


class _ChunkedContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.read_count = 0

    async def iter_chunked(self, _size: int):
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk


class UtilsDownloadTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _session_returning(resp: object) -> MagicMock:
        context_mock = AsyncMock()
        context_mock.__aenter__.return_value = resp
        session_mock = MagicMock()
        session_mock.get.return_value = context_mock
        return session_mock

    async def test_download_bytes_invalid_url(self) -> None:
        self.assertIsNone(await download_bytes(""))
        self.assertIsNone(await download_bytes("ftp://example.com/file"))

    async def test_download_bytes_rejects_oversized_content(self) -> None:
        for content_length, chunks in (
            (9, [b"12345678", b"9"]),
            (None, [b"12345", b"6789"]),
        ):
            with self.subTest(content_length=content_length):
                resp = SimpleNamespace(
                    status=200,
                    content_length=content_length,
                    content=_ChunkedContent(chunks),
                )

                with (
                    patch(
                        "astrbot_plugin_imgexploration.core.utils.get_aiohttp_session",
                        return_value=self._session_returning(resp),
                    ),
                    patch(
                        "astrbot_plugin_imgexploration.core.utils.MAX_DOWNLOAD_BYTES",
                        8,
                    ),
                    self.assertRaises(DownloadTooLargeError),
                ):
                    await download_bytes("https://example.com/large.jpg")

                # Content-Length 已超限时不读取响应内容
                self.assertEqual(
                    resp.content.read_count, 0 if content_length else len(chunks)
                )

    async def test_download_bytes_http_success_and_failure(self) -> None:
        session_mock = MagicMock()
        context_mock = AsyncMock()

        # Success case (status 200)
        resp_success = SimpleNamespace(
            status=200,
            content_length=13,
            content=_ChunkedContent([b"image_", b"content"]),
        )
        context_mock.__aenter__.return_value = resp_success

        session_mock.get.return_value = context_mock

        with patch(
            "astrbot_plugin_imgexploration.core.utils.get_aiohttp_session",
            return_value=session_mock,
        ):
            data = await download_bytes("https://example.com/test.jpg")
            self.assertEqual(data, b"image_content")

        # Status 404 case
        resp_404 = AsyncMock()
        resp_404.status = 404
        context_mock.__aenter__.return_value = resp_404
        with patch(
            "astrbot_plugin_imgexploration.core.utils.get_aiohttp_session",
            return_value=session_mock,
        ):
            self.assertIsNone(await download_bytes("https://example.com/404.jpg"))

    async def test_download_bytes_handles_request_exception(self) -> None:
        session_mock = MagicMock()
        session_mock.get.side_effect = RuntimeError("network unavailable")

        with patch(
            "astrbot_plugin_imgexploration.core.utils.get_aiohttp_session",
            return_value=session_mock,
        ):
            self.assertIsNone(await download_bytes("https://example.com/error.jpg"))


class UtilsPlatformAndBotTests(unittest.TestCase):
    def test_is_aiocqhttp_platform_uses_adapter_name(self) -> None:
        for platform_name, expected in (("aiocqhttp", True), ("telegram", False)):
            with self.subTest(platform_name=platform_name):
                event = SimpleNamespace(
                    # 平台实例的自定义 ID 或描述中含有 aiocqhttp 不影响判断
                    platform=f"name={platform_name} id=aiocqhttp-backup",
                    get_platform_name=lambda name=platform_name: name,
                )
                self.assertEqual(is_aiocqhttp_platform(event), expected)

    def test_get_bot_api(self) -> None:
        self.assertIsNone(get_bot_api(SimpleNamespace()))
        bot_obj = object()
        self.assertIs(get_bot_api(SimpleNamespace(bot=bot_obj)), bot_obj)


class UtilsReadImageBytesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.orig_local = is_local_file_access_allowed()
        self.orig_temp_images = is_astrbot_temp_images_allowed()
        self.addCleanup(set_allow_astrbot_temp_images, self.orig_temp_images)

    def tearDown(self) -> None:
        set_allow_local_file_access(self.orig_local)

    async def test_read_image_bytes_empty(self) -> None:
        self.assertIsNone(await read_image_bytes(""))

    async def test_read_image_bytes_http(self) -> None:
        with patch(
            "astrbot_plugin_imgexploration.core.utils.download_bytes",
            return_value=b"http_bytes",
        ):
            self.assertEqual(
                await read_image_bytes("https://example.com/test.png"), b"http_bytes"
            )

        with patch(
            "astrbot_plugin_imgexploration.core.utils.download_bytes",
            side_effect=DownloadTooLargeError,
        ):
            self.assertIsNone(await read_image_bytes("https://example.com/large.png"))

    async def test_read_image_bytes_local_file(self) -> None:
        set_allow_local_file_access(False)
        self.assertIsNone(await read_image_bytes("file:///tmp/test.png"))

        set_allow_local_file_access(True)

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir) / "my image.png"
            tmp_path.write_bytes(b"local_file_bytes")

            # Absolute file path
            data = await read_image_bytes(str(tmp_path))
            self.assertEqual(data, b"local_file_bytes")

            # Standard file URI with percent-encoded characters
            self.assertIn("%20", tmp_path.as_uri())
            self.assertEqual(
                await read_image_bytes(tmp_path.as_uri()), b"local_file_bytes"
            )

            # Non-standard file://C:/... style prefix
            file_url = "file://" + str(tmp_path).replace("\\", "/")
            data_url = await read_image_bytes(file_url)
            self.assertEqual(data_url, b"local_file_bytes")

            # Non-existent file and directory
            self.assertIsNone(await read_image_bytes(f"{tmp_path}_non_existent"))
            self.assertIsNone(await read_image_bytes(tmp_dir))

    async def test_read_image_bytes_supports_unescaped_legacy_file_uris(self) -> None:
        set_allow_local_file_access(True)

        with tempfile.TemporaryDirectory() as tmp_dir:
            directory = Path(tmp_dir).resolve()
            files = {
                "image#1.png": b"hash",
                "image%20v2.png": b"literal",
                "image v2.png": b"spaced",
            }
            for name, content in files.items():
                (directory / name).write_bytes(content)

            for name, content in files.items():
                path = directory / name
                # AstrBot 4.25 直接拼接未转义的绝对路径；4.26 起使用标准 URI
                for uri in (f"file:///{path}", path.as_uri()):
                    with self.subTest(uri=uri):
                        self.assertEqual(await read_image_bytes(uri), content)

    async def test_read_image_bytes_allows_only_astrbot_temp_images(self) -> None:
        set_allow_local_file_access(False)
        temp_root = use_isolated_astrbot_temp_dir(self)
        inside = temp_root / "media_image_test.jpg"
        inside.write_bytes(b"temp-image")
        escaped = temp_root.parent / "escaped.jpg"
        escaped.write_bytes(b"escaped")
        outside_dir = tempfile.TemporaryDirectory()
        self.addCleanup(outside_dir.cleanup)
        outside = Path(outside_dir.name) / "secret.jpg"
        outside.write_bytes(b"secret")

        set_allow_astrbot_temp_images(True)
        self.assertEqual(await read_image_bytes(str(inside)), b"temp-image")
        self.assertEqual(await read_image_bytes(inside.as_uri()), b"temp-image")
        # 目录外的文件和用 .. 跳出临时目录的路径仍受本地文件开关限制
        for source in (str(outside), f"{temp_root}/../escaped.jpg"):
            with self.subTest(source=source):
                self.assertIsNone(await read_image_bytes(source))

        set_allow_astrbot_temp_images(False)
        self.assertIsNone(await read_image_bytes(str(inside)))

    async def test_read_image_bytes_rejects_symlink_out_of_astrbot_temp(self) -> None:
        set_allow_local_file_access(False)
        set_allow_astrbot_temp_images(True)
        temp_root = use_isolated_astrbot_temp_dir(self)
        outside = temp_root.parent / "secret.jpg"
        outside.write_bytes(b"secret")
        link = temp_root / "media_image_link.jpg"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("当前环境不允许创建符号链接")

        self.assertIsNone(await read_image_bytes(str(link)))

    async def test_read_image_bytes_rejects_remote_paths(self) -> None:
        set_allow_local_file_access(True)

        # 使用 Windows 路径规则，在任何平台上都能检验 UNC 路径的拒绝逻辑
        for source in (
            "file://server/share/image.png",
            "file:////server/share/image.png",
            "//server/share/image.png",
        ):
            with (
                self.subTest(source=source),
                patch(
                    "astrbot_plugin_imgexploration.core.utils.Path",
                    PureWindowsPath,
                ),
                patch(
                    "astrbot_plugin_imgexploration.core.utils._read_file_bytes"
                ) as read_file,
            ):
                self.assertIsNone(await read_image_bytes(source))
                read_file.assert_not_called()

    async def test_read_image_bytes_limits_local_file_size(self) -> None:
        set_allow_local_file_access(True)

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch(
                "astrbot_plugin_imgexploration.core.utils.CATBOX_MAX_UPLOAD_BYTES",
                8,
            ),
        ):
            tmp_path = Path(tmp_dir) / "large.png"
            tmp_path.write_bytes(b"123456789")
            self.assertIsNone(await read_image_bytes(str(tmp_path)))

            # 报告的大小可能与实际输出不符（如设备文件），读取时同样受上限约束
            misreported = SimpleNamespace(
                is_file=lambda: True,
                stat=lambda: SimpleNamespace(st_size=0),
                open=lambda _mode: io.BytesIO(b"123456789"),
            )
            self.assertIsNone(_read_file_bytes(misreported))

    async def test_read_image_bytes_base64_and_data_uri(self) -> None:
        raw_payload = b"hello_base64"
        b64_str = base64.b64encode(raw_payload).decode("utf-8")

        # base64:// format
        self.assertEqual(await read_image_bytes(f"base64://{b64_str}"), raw_payload)
        self.assertIsNone(await read_image_bytes("base64://invalid_b64!!!"))

        # data URI format
        data_uri = f"data:image/png;base64,{b64_str}"
        self.assertEqual(await read_image_bytes(data_uri), raw_payload)
        self.assertIsNone(
            await read_image_bytes("data:image/png;base64,invalid_b64!!!")
        )


class UtilsUploadAndGetUrlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.orig_upload = is_image_upload_allowed()

    def tearDown(self) -> None:
        set_allow_image_upload(self.orig_upload)

    async def test_upload_image_validation_and_size_limit(self) -> None:
        self.assertIsNone(await upload_image(b""))

        # Mock large bytes (> 200MB)
        large_bytes = MagicMock()
        large_bytes.__len__.return_value = 201 * 1024 * 1024
        self.assertIsNone(await upload_image(large_bytes))

    async def test_upload_image_http_post_success_and_failure(self) -> None:
        session_mock = MagicMock()
        context_mock = AsyncMock()

        # Success case
        resp_success = AsyncMock()
        resp_success.status = 200
        resp_success.text = AsyncMock(return_value="https://files.catbox.moe/abc.jpg\n")
        context_mock.__aenter__.return_value = resp_success
        session_mock.post.return_value = context_mock

        with patch(
            "astrbot_plugin_imgexploration.core.utils.get_aiohttp_session",
            return_value=session_mock,
        ):
            uploaded_url = await upload_image(b"small_image")
            self.assertEqual(uploaded_url, "https://files.catbox.moe/abc.jpg")

        # Non-http response text case
        resp_invalid = AsyncMock()
        resp_invalid.status = 200
        resp_invalid.text = AsyncMock(return_value="Error: invalid file")
        context_mock.__aenter__.return_value = resp_invalid
        with patch(
            "astrbot_plugin_imgexploration.core.utils.get_aiohttp_session",
            return_value=session_mock,
        ):
            self.assertIsNone(await upload_image(b"small_image"))

    async def test_get_search_image_keeps_credential_urls_private(self) -> None:
        telegram_file_url = (
            "https://api.telegram.org/file/bot123456789:"
            "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ/photos/file_1.jpg"
        )
        self.assertEqual(
            await get_search_image("https://example.com/image.jpg"),
            "https://example.com/image.jpg",
        )

        # 携带凭据的地址只下载内容，下载失败时不回退到原地址
        for downloaded, expected in ((b"bytes", b"bytes"), (None, None)):
            with (
                self.subTest(downloaded=downloaded),
                patch(
                    "astrbot_plugin_imgexploration.core.utils.download_bytes",
                    new=AsyncMock(return_value=downloaded),
                ) as download,
            ):
                self.assertEqual(await get_search_image(telegram_file_url), expected)
            download.assert_awaited_once_with(telegram_file_url)

    async def test_get_search_image_reads_bytes_without_image_upload(self) -> None:
        set_allow_image_upload(False)
        with patch(
            "astrbot_plugin_imgexploration.core.utils.upload_image",
            new=AsyncMock(),
        ) as upload:
            self.assertEqual(await get_search_image("base64://YWJj"), b"abc")
            self.assertIsNone(await get_search_image(""))
        upload.assert_not_awaited()
