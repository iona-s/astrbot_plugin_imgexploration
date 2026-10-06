"""Google Lens 搜图策略实现.

通过 SerpAPI 调用 Google Lens 进行图片搜索。
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.parse

import aiohttp
from astrbot.api import logger

from ..constant import (
    DEFAULT_GOOGLE_LENS_AUTO_CROP,
    DEFAULT_GOOGLE_LENS_COUNTRY,
    DEFAULT_GOOGLE_LENS_LANGUAGE,
    DEFAULT_GOOGLE_LENS_MAX_RESULTS,
    DEFAULT_GOOGLE_LENS_NO_CACHE,
    DEFAULT_GOOGLE_LENS_SAFE_SEARCH,
    DEFAULT_GOOGLE_LENS_SEARCH_TYPE,
    HTTP_TIMEOUT_SECONDS,
    SERPAPI_BASE_URL,
)
from ..models import ProviderSearchError, SearchResultItem
from ..strategy import ImageSearchStrategy
from ..utils import get_aiohttp_session, get_proxy_url

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
        self._current_key_index = 0
        self._key_lock = asyncio.Lock()
        # 额度缓存: {api_key: (searches_left, timestamp)}
        self._quota_cache: dict[str, tuple[int, float]] = {}

    def get_service_name(self) -> str:
        return "Google Lens"

    async def search(self, image_url: str) -> list[SearchResultItem]:
        """执行 Google Lens 搜索.

        Args:
            image_url: 图片 URL 地址

        Returns:
            搜索结果列表
        """
        if not self.api_keys:
            logger.warning("[GoogleLens] 未配置 SerpAPI Key，跳过搜索")
            raise ProviderSearchError("未配置 SerpAPI Key")

        if not image_url.startswith(("http://", "https://")):
            logger.warning("[GoogleLens] SerpAPI 不支持本地文件")
            raise ProviderSearchError("SerpAPI 不支持本地文件")

        # 仅在额度耗尽时尝试下一个 Key；网络或服务端错误与 Key 无关，
        # 换 Key 重试只会叠加等待时间
        for attempt in range(len(self.api_keys)):
            try:
                return await self._search_with_key(image_url)
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

    async def _search_with_key(self, image_url: str) -> list[SearchResultItem]:
        """使用当前选中的 Key 执行搜索.

        Args:
            image_url: 图片 URL 地址

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

        # 构建 SerpAPI 请求
        params = {
            "api_key": api_key,
            "engine": "google_lens",
            "url": image_url,
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
                            description=source,
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
