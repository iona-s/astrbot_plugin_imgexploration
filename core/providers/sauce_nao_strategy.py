"""SauceNAO 搜图策略实现.

使用 SauceNAO API 进行图片搜索。
"""

from __future__ import annotations

import json
from typing import Any

import aiohttp
from astrbot.api import logger

from ..constant import (
    DEFAULT_SAUCENAO_MAX_RESULTS,
    HTTP_TIMEOUT_SECONDS,
    SAUCENAO_BASE_URL,
)
from ..models import SearchResultItem
from ..strategy import ImageSearchStrategy
from ..utils import get_aiohttp_session, get_proxy_url, get_user_agent


class SauceNaoStrategy(ImageSearchStrategy):
    """SauceNAO 搜图策略.

    SauceNAO 是一个强大的动漫图片搜索引擎，支持多个数据库。
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        similarity_threshold: int = 40,
        max_results: int = DEFAULT_SAUCENAO_MAX_RESULTS,
    ) -> None:
        """初始化 SauceNAO 策略.

        Args:
            api_key: SauceNAO API Key，可选但推荐使用以获得更高配额
            similarity_threshold: 相似度阈值 (0-100)，低于此值的结果将被过滤
            max_results: 最大结果数量
        """
        self.api_key = api_key
        self.similarity_threshold = max(0, min(100, similarity_threshold))
        self.max_results = max_results

    def get_service_name(self) -> str:
        return "SauceNAO"

    async def search(self, image_url: str) -> list[SearchResultItem]:
        """执行 SauceNAO 图片搜索.

        Args:
            image_url: 图片 URL 地址

        Returns:
            搜索结果列表
        """
        if not self.api_key:
            logger.warning("[SauceNAO] 未配置 API Key，跳过搜索")
            return []

        if not image_url.startswith(("http://", "https://")):
            logger.warning(f"[SauceNAO] 不支持的图片格式: {image_url}")
            return []

        results: list[SearchResultItem] = []

        try:
            session = await get_aiohttp_session()

            # 使用 URL 参数构建请求
            params = {
                "api_key": self.api_key,
                "output_type": "2",  # JSON 输出
                "numres": str(self.max_results),
                "url": image_url,
            }

            headers = {
                "User-Agent": get_user_agent(),
            }

            timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
            proxy = get_proxy_url()
            async with session.get(
                SAUCENAO_BASE_URL,
                params=params,
                headers=headers,
                timeout=timeout,
                proxy=proxy,
            ) as resp:
                if resp.status != 200:
                    logger.error(f"[SauceNAO] API 请求失败: HTTP {resp.status}")
                    return results

                text = await resp.text()
                json_data = json.loads(text)

                # 检查是否有错误
                if "results" not in json_data:
                    # 检查是否是错误响应
                    if "header" in json_data:
                        header = json_data["header"]
                        status = header.get("status", 0)
                        if status != 0:
                            message = header.get("message", "未知错误")
                            logger.error(f"[SauceNAO] API 错误: {message}")
                    return results

                for node in json_data["results"]:
                    header = node.get("header", {})
                    data_section = node.get("data", {})

                    # 提取相似度
                    similarity_str = header.get("similarity", "0")
                    try:
                        similarity = float(similarity_str)
                    except ValueError:
                        similarity = 0.0

                    # 相似度阈值过滤
                    # SauceNAO 的相似度计算方式，阈值越低结果越多
                    if similarity < self.similarity_threshold:
                        continue

                    # 提取标题
                    title = self._extract_title(data_section)

                    # 提取外部链接
                    ext_urls = data_section.get("ext_urls", [])
                    ext_url = ext_urls[0] if ext_urls else ""

                    # 提取缩略图 URL
                    thumbnail = header.get("thumbnail", "")

                    results.append(
                        SearchResultItem(
                            title=title,
                            url=ext_url,
                            thumbnail=thumbnail,
                            thumbnail_bytes=None,
                            source="SauceNAO",
                            similarity=f"{similarity:.2f}%",
                            description=None,
                            domain=None,
                        )
                    )
                    if len(results) >= self.max_results:
                        break

        except Exception as e:
            logger.error(f"[SauceNAO] 搜索失败: {e}")

        return results

    @staticmethod
    def _extract_title(data: dict[str, Any]) -> str:
        """从 SauceNAO 数据中提取标题.

        Args:
            data: SauceNAO 结果的 data 部分

        Returns:
            提取的标题字符串
        """
        # 按优先级尝试不同的标题字段
        for key in ("title", "eng_name", "jp_name", "material", "source"):
            if value := data.get(key):
                return value

        # Pixiv 作者名
        if member_name := data.get("member_name"):
            return f"Artist: {member_name}"

        return "SauceNAO Result"
