from __future__ import annotations

import base64
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from astrbot_plugin_imgexploration.core.constant import DEFAULT_USER_AGENT
from astrbot_plugin_imgexploration.core.utils import (
    _sanitize_url_for_logging,
    close_aiohttp_session,
    download_bytes,
    download_bytes_batch,
    get_aiohttp_session,
    get_bot_api,
    get_http_image_url,
    get_proxy_url,
    get_user_agent,
    is_aiocqhttp_platform,
    is_image_upload_allowed,
    is_local_file_access_allowed,
    read_image_bytes,
    set_allow_image_upload,
    set_allow_local_file_access,
    set_proxy_url,
    set_user_agent,
    upload_image,
)


class UtilsGlobalConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.orig_proxy = get_proxy_url()
        self.orig_ua = get_user_agent()
        self.orig_upload = is_image_upload_allowed()
        self.orig_local = is_local_file_access_allowed()

    def tearDown(self) -> None:
        set_proxy_url(self.orig_proxy)
        set_user_agent(self.orig_ua if self.orig_ua != DEFAULT_USER_AGENT else None)
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

    def test_user_agent_getter_setter(self) -> None:
        self.assertEqual(get_user_agent(), DEFAULT_USER_AGENT)

        set_user_agent("CustomUA/1.0")
        self.assertEqual(get_user_agent(), "CustomUA/1.0")

        set_user_agent("")
        self.assertEqual(get_user_agent(), DEFAULT_USER_AGENT)

    def test_allow_flags_getters_setters(self) -> None:
        set_allow_image_upload(False)
        self.assertFalse(is_image_upload_allowed())
        set_allow_image_upload(True)
        self.assertTrue(is_image_upload_allowed())

        set_allow_local_file_access(True)
        self.assertTrue(is_local_file_access_allowed())
        set_allow_local_file_access(False)
        self.assertFalse(is_local_file_access_allowed())


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


class UtilsDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_bytes_invalid_url(self) -> None:
        self.assertIsNone(await download_bytes(""))
        self.assertIsNone(await download_bytes("ftp://example.com/file"))

    async def test_download_bytes_http_success_and_failure(self) -> None:
        session_mock = MagicMock()
        context_mock = AsyncMock()

        # Success case (status 200)
        resp_success = AsyncMock()
        resp_success.status = 200
        resp_success.read = AsyncMock(return_value=b"image_content")
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

    async def test_download_bytes_batch(self) -> None:
        with patch(
            "astrbot_plugin_imgexploration.core.utils.download_bytes"
        ) as mock_dl:
            mock_dl.side_effect = [b"data1", b"data2"]
            results = await download_bytes_batch(["https://url1", "https://url2"])
            self.assertEqual(results, [b"data1", b"data2"])


class UtilsPlatformAndBotTests(unittest.TestCase):
    def test_is_aiocqhttp_platform(self) -> None:
        self.assertFalse(is_aiocqhttp_platform(SimpleNamespace()))
        self.assertFalse(is_aiocqhttp_platform(SimpleNamespace(platform="telegram")))
        self.assertTrue(is_aiocqhttp_platform(SimpleNamespace(platform="aiocqhttp")))
        self.assertTrue(
            is_aiocqhttp_platform(SimpleNamespace(platform="AIOCQHTTP_V11"))
        )

    def test_get_bot_api(self) -> None:
        self.assertIsNone(get_bot_api(SimpleNamespace()))
        bot_obj = object()
        self.assertIs(get_bot_api(SimpleNamespace(bot=bot_obj)), bot_obj)


class UtilsReadImageBytesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.orig_local = is_local_file_access_allowed()

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

    async def test_read_image_bytes_local_file(self) -> None:
        set_allow_local_file_access(False)
        self.assertIsNone(await read_image_bytes("file:///tmp/test.png"))

        set_allow_local_file_access(True)

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"local_file_bytes")
            tmp_path = tmp.name

        try:
            # Absolute file path
            data = await read_image_bytes(tmp_path)
            self.assertEqual(data, b"local_file_bytes")

            # file:// prefix
            file_url = "file://" + tmp_path.replace("\\", "/")
            data_url = await read_image_bytes(file_url)
            self.assertEqual(data_url, b"local_file_bytes")

            # Non-existent file
            self.assertIsNone(await read_image_bytes(tmp_path + "_non_existent"))
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

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

    async def test_get_http_image_url(self) -> None:
        self.assertIsNone(await get_http_image_url(""))

        # Pass HTTP URL directly
        self.assertEqual(
            await get_http_image_url("https://example.com/image.jpg"),
            "https://example.com/image.jpg",
        )

        # Upload disabled
        set_allow_image_upload(False)
        self.assertIsNone(await get_http_image_url("base64://abc"))

        # Upload enabled
        set_allow_image_upload(True)
        with (
            patch(
                "astrbot_plugin_imgexploration.core.utils.read_image_bytes",
                return_value=b"bytes",
            ),
            patch(
                "astrbot_plugin_imgexploration.core.utils.upload_image",
                return_value="https://uploaded.moe/1.jpg",
            ),
        ):
            url = await get_http_image_url("base64://abc")
            self.assertEqual(url, "https://uploaded.moe/1.jpg")
