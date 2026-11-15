"""Google Lens 搜图策略实现.

通过 SerpAPI 调用 Google Lens 进行图片搜索。
"""

from __future__ import annotations

import asyncio
import io
import json
import time
import urllib.parse

import aiohttp
from astrbot.api import logger
from PIL import Image, ImageOps

from ..constant import (
    DEFAULT_GOOGLE_LENS_AUTO_CROP,
    DEFAULT_GOOGLE_LENS_COUNTRY,
    DEFAULT_GOOGLE_LENS_LANGUAGE,
    DEFAULT_GOOGLE_LENS_MAX_RESULTS,
    DEFAULT_GOOGLE_LENS_NO_CACHE,
    DEFAULT_GOOGLE_LENS_SAFE_SEARCH,
    DEFAULT_GOOGLE_LENS_SEARCH_TYPE,
    DEFAULT_GOOGLE_LENS_UNSUPPORTED_IMAGE,
    GOOGLE_LENS_UNSUPPORTED_IMAGE_COMPRESS,
    GOOGLE_LENS_UNSUPPORTED_IMAGE_HOST,
    HTTP_TIMEOUT_SECONDS,
    SERPAPI_BASE_URL,
    SERPAPI_IMAGE_UPLOAD_URL,
    SERPAPI_MAX_UPLOAD_BYTES,
)
from ..models import ProviderSearchError, SearchResultItem
from ..strategy import ImageSearchStrategy
from ..utils import get_aiohttp_session, get_proxy_url, redact_secrets, upload_image

# 额度缓存 TTL（秒）
QUOTA_CACHE_TTL = 60
GOOGLE_LENS_SEARCH_TYPES = {
    "all",
    "exact_matches",
    "products",
    "visual_matches",
}


def _normalize_bool(value: object, *, default: bool) -> bool:
    """将配置值规范化为布尔值，无效值回退到默认值."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return default


def _fits_serpapi_upload(image: bytes) -> bool:
    """SerpAPI 只接受 500 KB 以内的 JPG、PNG、WebP."""
    return len(image) <= SERPAPI_MAX_UPLOAD_BYTES and (
        image.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n"))
        or (image[:4] == b"RIFF" and image[8:12] == b"WEBP")
    )


def _compress_for_serpapi(image: bytes) -> bytes | None:
    """把图片转为 JPEG 并逐步缩小，直到满足 SerpAPI 上传限制.

    动图只取第一帧，透明区域填充为白色。

    Args:
        image: 原始图片字节

    Returns:
        压缩后的 JPEG 字节；无法识别或缩小到 256 像素仍超限时返回 None
    """
    try:
        with Image.open(io.BytesIO(image)) as opened:
            # 按 EXIF 方向摆正手机照片，避免上传后图片是横躺的
            frame = ImageOps.exif_transpose(opened)
            rgba = frame.convert("RGBA")
            source = Image.new("RGB", rgba.size, "white")
            source.paste(rgba, mask=rgba.getchannel("A"))
    except Exception as e:
        logger.warning(f"[GoogleLens] 无法识别图片，压缩失败: {type(e).__name__}")
        return None

    # 至少按原尺寸编码一次：小图往往只是格式不受支持，转为 JPEG 即可
    side = min(max(source.size), 2048)
    while True:
        candidate = source.copy()
        candidate.thumbnail((side, side), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        candidate.save(buffer, "JPEG", quality=85, optimize=True)
        size = buffer.tell()
        if size <= SERPAPI_MAX_UPLOAD_BYTES:
            return buffer.getvalue()
        if side <= 256:
            break
        # JPEG 体积大致与像素数成正比，按比例估算下一轮边长，减少重复编码
        side = max(
            256,
            int(side * min(0.9, (SERPAPI_MAX_UPLOAD_BYTES / size) ** 0.5 * 0.95)),
        )
    logger.warning("[GoogleLens] 图片压缩后仍超过 SerpAPI 上传限制")
    return None


class SerpApiQuotaExhaustedError(RuntimeError):
    """SerpAPI Key 额度耗尽异常."""

    def __init__(self, api_key: str, status: int | None = None) -> None:
        self.api_key = api_key
        self.status = status
        super().__init__(f"SerpAPI key exhausted: ...{api_key[-4:]} (status={status})")


class GoogleLensStrategy(ImageSearchStrategy):
    """Google Lens 搜图策略.

    使用 SerpAPI 的 google_lens 引擎进行图片搜索。
    支持多 API Key 负载均衡和余额检查。
    """

    def __init__(
        self,
        *,
        api_keys: list[str] | None = None,
        max_results: int = DEFAULT_GOOGLE_LENS_MAX_RESULTS,
        search_type: str = DEFAULT_GOOGLE_LENS_SEARCH_TYPE,
        language: str = DEFAULT_GOOGLE_LENS_LANGUAGE,
        country: str = DEFAULT_GOOGLE_LENS_COUNTRY,
        safe_search: bool | str = DEFAULT_GOOGLE_LENS_SAFE_SEARCH,
        auto_crop: bool | str = DEFAULT_GOOGLE_LENS_AUTO_CROP,
        no_cache: bool | str = DEFAULT_GOOGLE_LENS_NO_CACHE,
        unsupported_image: str = DEFAULT_GOOGLE_LENS_UNSUPPORTED_IMAGE,
    ) -> None:
        """初始化 Google Lens 策略.

        Args:
            api_keys: SerpAPI API Key 列表，支持多 Key 负载均衡
            max_results: 最大结果数量
            search_type: Google Lens 搜索类型
            language: 搜索结果语言代码
            country: 搜索结果国家代码，留空时由 Google 决定
            safe_search: 是否启用严格的成人内容过滤；关闭时使用 Google 默认处理
            auto_crop: 是否让 Google 自动裁剪图片主体
            no_cache: 是否绕过 SerpAPI 一小时缓存
            unsupported_image: 图片不满足 SerpAPI 上传限制时压缩，还是上传到图床
        """
        self.api_keys = api_keys or []
        self.max_results = max_results
        normalized_search_type = str(search_type or "").strip().lower()
        self.search_type = (
            normalized_search_type
            if normalized_search_type in GOOGLE_LENS_SEARCH_TYPES
            else DEFAULT_GOOGLE_LENS_SEARCH_TYPE
        )
        self.language = (
            str(language or "").strip().lower() or DEFAULT_GOOGLE_LENS_LANGUAGE
        )
        self.country = str(country or "").strip().lower()
        self.safe_search = _normalize_bool(
            safe_search, default=DEFAULT_GOOGLE_LENS_SAFE_SEARCH
        )
        self.auto_crop = _normalize_bool(
            auto_crop, default=DEFAULT_GOOGLE_LENS_AUTO_CROP
        )
        self.no_cache = _normalize_bool(no_cache, default=DEFAULT_GOOGLE_LENS_NO_CACHE)
        normalized_unsupported_image = str(unsupported_image or "").strip().lower()
        self.unsupported_image = (
            normalized_unsupported_image
            if normalized_unsupported_image
            in {
                GOOGLE_LENS_UNSUPPORTED_IMAGE_COMPRESS,
                GOOGLE_LENS_UNSUPPORTED_IMAGE_HOST,
            }
            else DEFAULT_GOOGLE_LENS_UNSUPPORTED_IMAGE
        )
        self._current_key_index = 0
        self._key_lock = asyncio.Lock()
        # 额度缓存: {api_key: (searches_left, timestamp)}
        self._quota_cache: dict[str, tuple[int, float]] = {}

    def get_service_name(self) -> str:
        return "Google Lens"

    async def search(self, image: str | bytes) -> list[SearchResultItem]:
        """执行 Google Lens 搜索.

        Args:
            image: 图片 URL 地址，或先上传到 SerpAPI 的图片字节

        Returns:
            搜索结果列表
        """
        if not self.api_keys:
            logger.warning("[GoogleLens] 未配置 SerpAPI Key，跳过搜索")
            raise ProviderSearchError("未配置 SerpAPI Key")

        if isinstance(image, str) and not image.startswith(("http://", "https://")):
            logger.warning("[GoogleLens] SerpAPI 不支持本地文件")
            raise ProviderSearchError("SerpAPI 不支持本地文件")

        # SerpAPI 只接受 500 KB 以内的 JPG、PNG、WebP，其他图片按配置压缩或改用图床地址
        if isinstance(image, bytes) and not _fits_serpapi_upload(image):
            if self.unsupported_image == GOOGLE_LENS_UNSUPPORTED_IMAGE_HOST:
                image_url = await upload_image(image)
                if not image_url:
                    raise ProviderSearchError(
                        "图床上传失败", user_notice="[Google Lens] 图片上传图床失败"
                    )
                image = image_url
            else:
                compressed = await asyncio.to_thread(_compress_for_serpapi, image)
                if compressed is None:
                    raise ProviderSearchError("图片压缩失败")
                logger.debug(
                    f"[GoogleLens] 已压缩图片: {len(image)} -> {len(compressed)} 字节"
                )
                image = compressed

        # 仅在额度耗尽时尝试下一个 Key；网络或服务端错误与 Key 无关，
        # 换 Key 重试只会叠加等待时间
        for attempt in range(len(self.api_keys)):
            try:
                return await self._search_with_key(image)
            except SerpApiQuotaExhaustedError as e:
                if not e.api_key:
                    # 所有 Key 都处于额度耗尽缓存中，无需继续尝试
                    break
                logger.warning(
                    f"[GoogleLens] Key ...{e.api_key[-4:]} 额度已耗尽，"
                    f"尝试使用下一个可用 Key (尝试 {attempt + 1}/{len(self.api_keys)})"
                )
                # 继续尝试下一个 key
                continue
            except ProviderSearchError:
                raise
            except Exception as e:
                # 异常文本可能包含带 api_key 的请求 URL，因此只记录异常类型
                logger.error(f"[GoogleLens] 搜索失败: {type(e).__name__}")
                raise ProviderSearchError(f"搜索失败: {type(e).__name__}") from e

        # 所有 Key 额度都已耗尽
        logger.error("[GoogleLens] 所有 API Key 已耗尽")
        raise ProviderSearchError("所有 API Key 已耗尽")

    async def _upload_image(self, api_key: str, image: bytes) -> str:
        """把图片上传到 SerpAPI，换取搜索用的 image_id.

        上传不消耗搜索额度；image_id 10 分钟后失效，因此每次搜索都重新上传。

        Args:
            api_key: 本次搜索使用的 SerpAPI Key
            image: 图片字节

        Returns:
            SerpAPI 返回的 image_id

        Raises:
            SerpApiQuotaExhaustedError: Key 无效或受限时抛出
            ProviderSearchError: 上传被拒绝或响应缺少 image_id 时抛出
        """
        form = aiohttp.FormData()
        form.add_field("api_key", api_key)
        form.add_field(
            "image", image, filename="image", content_type="application/octet-stream"
        )
        session = await get_aiohttp_session()
        async with session.post(
            SERPAPI_IMAGE_UPLOAD_URL,
            data=form,
            timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS),
            proxy=get_proxy_url(),
        ) as resp:
            if resp.status in (401, 403, 429):
                await self._mark_key_exhausted(api_key)
                raise SerpApiQuotaExhaustedError(api_key, status=resp.status)
            data = await resp.json(content_type=None)

        image_id = data.get("image_id") if isinstance(data, dict) else None
        if resp.status != 200 or not isinstance(image_id, str) or not image_id:
            error = data.get("error") if isinstance(data, dict) else None
            message = redact_secrets(str(error or f"HTTP {resp.status}"))
            logger.error(f"[GoogleLens] 图片上传失败: {message}")
            raise ProviderSearchError(f"图片上传失败: {message}")
        return image_id

    async def _search_with_key(self, image: str | bytes) -> list[SearchResultItem]:
        """使用当前选中的 Key 执行搜索.

        Args:
            image: 图片 URL 地址，或图片字节

        Returns:
            搜索结果列表

        Raises:
            SerpApiQuotaExhaustedError: 当 API Key 额度耗尽时抛出
        """
        # 选择可用的 API Key（乐观选择，不预先检查额度）
        api_key = await self._select_key_optimistically()
        if not api_key:
            raise SerpApiQuotaExhaustedError("", status=None)

        logger.info(f"[GoogleLens] 使用 Key ...{api_key[-4:]} 开始搜索")

        # 图片字节先上传到 SerpAPI，用 image_id 代替 url 搜索
        image_param = (
            {"image_id": await self._upload_image(api_key, image)}
            if isinstance(image, bytes)
            else {"url": image}
        )

        # 构建 SerpAPI 请求
        params = {
            "api_key": api_key,
            "engine": "google_lens",
            **image_param,
            "type": self.search_type,
            "hl": self.language,
            "auto_crop": str(self.auto_crop).lower(),
            "no_cache": str(self.no_cache).lower(),
        }
        if self.country:
            params["country"] = self.country
        # 关闭时不发送 safe，保持 Google 默认的模糊处理
        if self.safe_search:
            params["safe"] = "active"

        url = f"{SERPAPI_BASE_URL}/search?{urllib.parse.urlencode(params)}"

        session = await get_aiohttp_session()
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
        proxy = get_proxy_url()

        async with session.get(url, timeout=timeout, proxy=proxy) as resp:
            if resp.status != 200:
                # 处理额度耗尽错误：标记当前 key 耗尽，并抛出异常让上层重试
                if resp.status in (401, 403, 429):
                    await self._mark_key_exhausted(api_key)
                    raise SerpApiQuotaExhaustedError(api_key, status=resp.status)
                logger.error(f"[GoogleLens] API 返回错误: HTTP {resp.status}")
                raise ProviderSearchError(f"API 返回错误: HTTP {resp.status}")

            text = await resp.text()
            data = json.loads(text)

        # 检查响应中的错误
        if "error" in data:
            error_msg = data.get("error", "")
            if "hasn't returned any results" in error_msg.lower():
                logger.info("[GoogleLens] SerpAPI 未返回匹配结果")
                return []

            if "API key" in error_msg or "exceeded" in error_msg.lower():
                await self._mark_key_exhausted(api_key)
                raise SerpApiQuotaExhaustedError(api_key, status=None)
            # 服务端错误信息可能回显请求的图片地址
            error_msg = redact_secrets(error_msg)
            logger.error(f"[GoogleLens] SerpAPI 错误: {error_msg}")
            raise ProviderSearchError(f"SerpAPI 错误: {error_msg}")

        # 解析结果；跳过缺少标题或链接的项，直到取满结果上限
        results = []
        result_key = (
            "visual_matches"
            if self.search_type in {"all", "products"}
            else self.search_type
        )
        matches = data.get(result_key, [])
        if isinstance(matches, list):
            for match in matches:
                if len(results) >= self.max_results:
                    break
                try:
                    title = match.get("title", "")
                    link = match.get("link", "")
                    source = match.get("source", "")
                    thumbnail = match.get("thumbnail", "")

                    if not title or not link:
                        continue

                    results.append(
                        SearchResultItem(
                            title=title,
                            url=link,
                            thumbnail=thumbnail,
                            thumbnail_bytes=None,  # 缩略图由服务层统一下载
                            source="Google Lens",
                            similarity=None,
                            # 站点名加标签，避免在结果中显示为无说明的一行
                            description=f"站点: {source}" if source else None,
                            domain=None,
                        )
                    )
                except Exception as e:
                    logger.warning(f"[GoogleLens] 解析结果项失败: {e}")

        logger.info(f"[GoogleLens] 搜索完成，获取 {len(results)} 条结果")
        return results

    async def _select_key_optimistically(self) -> str | None:
        """乐观选择 API Key，不预先检查额度.

        使用轮询方式选择 Key，额度错误在实际请求时处理。
        使用缓存避免短时间内重复检查已知耗尽的 Key。

        Returns:
            可用的 API Key，如果没有则返回 None
        """
        if not self.api_keys:
            return None

        async with self._key_lock:
            # 清理过期的缓存
            now = time.time()
            expired_keys = [
                k
                for k, (_, ts) in self._quota_cache.items()
                if now - ts > QUOTA_CACHE_TTL
            ]
            for k in expired_keys:
                del self._quota_cache[k]

            start_idx = self._current_key_index % len(self.api_keys)

            for i in range(len(self.api_keys)):
                idx = (start_idx + i) % len(self.api_keys)
                key = self.api_keys[idx]

                # 检查缓存中是否有额度信息
                cached = self._quota_cache.get(key)
                if cached:
                    searches_left, _ = cached
                    if searches_left <= 0:
                        continue  # 缓存显示已耗尽，跳过

                self._current_key_index = (idx + 1) % len(self.api_keys)
                return key

            return None

    async def _mark_key_exhausted(self, api_key: str) -> None:
        """标记 API Key 已耗尽.

        Args:
            api_key: 已耗尽的 API Key
        """
        async with self._key_lock:
            self._quota_cache[api_key] = (0, time.time())
            logger.debug(f"[GoogleLens] 已标记 Key ...{api_key[-4:]} 为耗尽状态")
