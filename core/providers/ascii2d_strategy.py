"""Ascii2d 搜图策略实现.

通过 Ascii2d 网站进行图片搜索，支持 color 和 bovw 两种搜索模式。
使用 curl_cffi 模拟浏览器 TLS 指纹绕过 Cloudflare。
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import urllib.parse

from astrbot.api import logger
from curl_cffi.requests import AsyncSession

from ..constant import (
    ASCII2D_BASE_URL,
    ASCII2D_SEARCH_URI_URL,
    DEFAULT_ASCII2D_BOVW_MAX_RESULTS,
    DEFAULT_ASCII2D_COLOR_MAX_RESULTS,
    HTTP_TIMEOUT_SECONDS,
)
from ..models import SearchResultItem
from ..strategy import ImageSearchStrategy
from ..utils import download_bytes, get_proxy_url


class Ascii2dStrategy(ImageSearchStrategy):
    """Ascii2d 搜图策略.

    支持 HTTP 链接搜索，返回 color 和 bovw 搜索结果。
    使用 curl_cffi 模拟 Chrome 浏览器 TLS 指纹。
    """

    # 模拟 Chrome 浏览器 (curl_cffi 支持的稳定版本)
    IMPERSONATE_BROWSER = "chrome120"

    def __init__(
        self,
        *,
        session_id: str | None = None,
        cf_clearance: str | None = None,
        bovw_max_results: int = DEFAULT_ASCII2D_BOVW_MAX_RESULTS,
        color_max_results: int = DEFAULT_ASCII2D_COLOR_MAX_RESULTS,
    ) -> None:
        """初始化 Ascii2d 策略.

        Args:
            session_id: Ascii2d 网站的 _session_id Cookie 值
            cf_clearance: Cloudflare cf_clearance Cookie 值，用于绕过 CF 验证
            bovw_max_results: BOVW 搜索最大结果数量
            color_max_results: 色合搜索最大结果数量
        """
        self.session_id = session_id or ""
        self.cf_clearance = cf_clearance or ""
        self.bovw_max_results = bovw_max_results
        self.color_max_results = color_max_results
        # 共享的 curl_cffi AsyncSession，避免重复创建连接
        self._session: AsyncSession | None = None
        self._session_lock = asyncio.Lock()

    async def _get_session(self) -> AsyncSession:
        """获取共享的 AsyncSession 实例.

        懒初始化，首次调用时创建，后续复用。

        Returns:
            共享的 AsyncSession 实例
        """
        if self._session is not None:
            return self._session

        async with self._session_lock:
            if self._session is None:
                proxies = self._get_proxies()
                self._session = AsyncSession(
                    impersonate=self.IMPERSONATE_BROWSER,
                    proxies=proxies,
                    timeout=HTTP_TIMEOUT_SECONDS,
                )
            return self._session

    async def close(self) -> None:
        """关闭共享的 AsyncSession 并清理资源."""
        async with self._session_lock:
            if self._session is not None:
                await self._session.close()
                self._session = None
                logger.debug("[Ascii2d] AsyncSession 已关闭")

    def _get_cookies(self) -> dict:
        """获取 Cookie 字典.

        Returns:
            Cookie 字典
        """
        cookies = {}
        if self.cf_clearance:
            cookies["cf_clearance"] = self.cf_clearance
        if self.session_id:
            cookies["_session_id"] = self.session_id
        return cookies

    def _get_proxies(self) -> dict | None:
        """获取代理配置.

        Returns:
            代理配置字典，无代理返回 None
        """
        proxy_url = get_proxy_url()
        if proxy_url:
            return {"http": proxy_url, "https": proxy_url}
        return None

    def get_service_name(self) -> str:
        return "Ascii2d"

    async def search(self, image_url: str) -> list[SearchResultItem]:
        """执行 Ascii2d 搜索.

        Args:
            image_url: 图片 URL 地址

        Returns:
            搜索结果列表
        """
        if not image_url.startswith(("http://", "https://")):
            logger.warning("[Ascii2d] 仅支持 HTTP URL")
            return []

        try:
            # 步骤 1: 获取 authenticity_token
            token = await self._fetch_authenticity_token()
            if not token:
                logger.error("[Ascii2d] 获取 token 失败")
                return []

            # 步骤 2: 提交搜索请求，获取结果页 URL
            result_url = await self._post_url_search(image_url, token)
            if not result_url:
                logger.error("[Ascii2d] 搜索请求失败")
                return []

            # 步骤 3: 并行获取 color 和 bovw 结果
            color_results, bovw_results = await asyncio.gather(
                self._fetch_and_parse_result_page(result_url, is_bovw=False),
                self._fetch_and_parse_result_page(result_url, is_bovw=True),
            )

            # 合并结果：优先 bovw，再 color
            combined = []
            combined.extend(bovw_results[: self.bovw_max_results])
            combined.extend(color_results[: self.color_max_results])

            # 下载缩略图
            thumbnail_urls = [item.thumbnail for item in combined if item.thumbnail]
            thumbnail_bytes_list = await asyncio.gather(
                *[download_bytes(url) for url in thumbnail_urls],
                return_exceptions=False,
            )

            # 回填缩略图字节
            final_results = []
            thumbnail_idx = 0
            for item in combined:
                thumbnail_bytes = None
                if item.thumbnail and thumbnail_idx < len(thumbnail_bytes_list):
                    thumbnail_bytes = thumbnail_bytes_list[thumbnail_idx]
                    thumbnail_idx += 1

                final_results.append(
                    SearchResultItem(
                        title=item.title,
                        url=item.url,
                        thumbnail=item.thumbnail,
                        thumbnail_bytes=thumbnail_bytes,
                        source="Ascii2d",
                        similarity=None,
                        description=None,
                        domain=None,
                    )
                )

            logger.info(f"[Ascii2d] 搜索完成，获取 {len(final_results)} 条结果")
            return final_results

        except Exception as e:
            logger.error(f"[Ascii2d] 搜索异常: {e}")
            return []

    async def _fetch_authenticity_token(self) -> str | None:
        """从 Ascii2d 主页获取 authenticity_token.

        Returns:
            token 字符串，失败返回 None
        """
        cookies = self._get_cookies()

        try:
            session = await self._get_session()
            response = await session.get(
                ASCII2D_BASE_URL,
                cookies=cookies,
            )

            if response.status_code != 200:
                logger.warning(f"[Ascii2d] 获取主页失败: HTTP {response.status_code}")
                logger.debug(
                    f"[Ascii2d] 响应内容: {response.text[:500] if response.text else 'empty'}"
                )
                return None

            html = response.text

        except Exception as e:
            logger.error(f"[Ascii2d] 获取主页异常: {e}")
            return None

        # 解析 token - 尝试多种模式
        patterns = [
            r'name="authenticity_token"\s+value="([^"]+)"',
            r'value="([^"]+)"\s*name="authenticity_token"',
            r'authenticity_token"\s+value="([^"]+)"',
            r'<input[^>]*name="authenticity_token"[^>]*value="([^"]+)"',
            r'<input[^>]*value="([^"]+)"[^>]*name="authenticity_token"',
        ]

        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                logger.debug("[Ascii2d] 成功获取 authenticity_token")
                return match.group(1)

        logger.warning(
            "[Ascii2d] 未找到 authenticity_token，可能网页结构已变化或 Session ID 无效"
        )
        logger.debug(f"[Ascii2d] HTML 片段: {html[:500] if html else 'empty'}")
        return None

    async def _post_url_search(self, image_url: str, token: str) -> str | None:
        """提交 URL 搜索请求.

        Args:
            image_url: 图片 URL
            token: authenticity_token

        Returns:
            结果页 URL，失败返回 None
        """
        cookies = self._get_cookies()

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": ASCII2D_BASE_URL,
            "Referer": f"{ASCII2D_BASE_URL}/",
        }

        # 手动构建表单数据
        form_data = urllib.parse.urlencode(
            {
                "utf8": "✓",
                "authenticity_token": token,
                "uri": image_url,
                "search": "",
            }
        )

        try:
            session = await self._get_session()
            response = await session.post(
                ASCII2D_SEARCH_URI_URL,
                data=form_data,
                cookies=cookies,
                headers=headers,
                allow_redirects=True,
            )

            if response.status_code == 200:
                final_url = str(response.url)
                # 验证是否跳转到结果页
                if "/search/color/" in final_url or "/search/bovw/" in final_url:
                    logger.info(f"[Ascii2d] 搜索成功，结果页: {final_url}")
                    return final_url
                logger.warning(f"[Ascii2d] 重定向到非预期 URL: {final_url}")
                return final_url

            logger.warning(f"[Ascii2d] POST 失败: HTTP {response.status_code}")
            logger.debug(
                f"[Ascii2d] POST 响应: {response.text[:500] if response.text else 'empty'}"
            )
            return None

        except Exception as e:
            logger.error(f"[Ascii2d] POST 请求异常: {e}")
            return None

    async def _fetch_and_parse_result_page(
        self, base_url: str, is_bovw: bool
    ) -> list[SearchResultItem]:
        """获取并解析结果页.

        Args:
            base_url: 结果页基础 URL
            is_bovw: 是否为 bovw 模式

        Returns:
            解析后的结果列表（不含缩略图字节）
        """
        # 构建目标 URL
        if is_bovw:
            target_url = base_url.replace("/color/", "/bovw/")
        elif "/color/" not in base_url:
            target_url = base_url.replace("/bovw/", "/color/")
        else:
            target_url = base_url

        cookies = self._get_cookies()
        # 模拟浏览器直接访问结果页（无 Referer）
        # 注意：curl_cffi 会在重定向时自动处理 Referer，不要手动设置
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "max-age=0",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }

        try:
            session = await self._get_session()
            response = await session.get(
                target_url,
                cookies=cookies,
                headers=headers,
            )

            if response.status_code != 200:
                logger.warning(f"[Ascii2d] 获取结果页失败: HTTP {response.status_code}")
                return []

            html = response.text

        except Exception as e:
            logger.error(f"[Ascii2d] 获取结果页异常: {e}")
            return []

        return self._parse_ascii2d_html(html)

    @staticmethod
    def _parse_ascii2d_html(html: str) -> list[SearchResultItem]:
        """解析 Ascii2d HTML 结果页.

        Args:
            html: HTML 内容

        Returns:
            解析后的结果列表
        """
        results = []

        # 匹配 item-box 块 (从 <div class='row item-box'> 到 <div class='clearfix'></div>)
        # HTML 使用单引号，需要同时支持单引号和双引号
        item_boxes = re.findall(
            r"<div\s+class=['\"]row\s+item-box['\"][^>]*>.*?<div\s+class=['\"]clearfix['\"]></div>",
            html,
            re.DOTALL,
        )

        # 跳过第一个（通常是搜索原图）
        for box in item_boxes[1:]:
            try:
                # 提取缩略图 (支持单引号和双引号)
                thumbnail_match = re.search(r"<img[^>]+src=['\"]([^'\"]+)['\"]", box)
                thumbnail = ""
                if thumbnail_match:
                    thumb_src = thumbnail_match.group(1)
                    if not thumb_src.startswith("http"):
                        thumbnail = f"{ASCII2D_BASE_URL}{thumb_src}"
                    else:
                        thumbnail = thumb_src

                # 提取标题和链接 (在 h6 内的 a 标签中，支持单引号和双引号)
                # 结构: <h6>...<a href="...">标题</a>...
                title_match = re.search(
                    r"<h6[^>]*>.*?<a[^>]+href=['\"]([^'\"]+)['\"][^>]*>([^<]+)</a>",
                    box,
                    re.DOTALL,
                )
                if not title_match:
                    continue

                url = title_match.group(1)
                title = title_match.group(2).strip()

                # 尝试解码 URL
                with contextlib.suppress(Exception):
                    url = urllib.parse.unquote(url)

                # 如果链接是相对路径，尝试提取其他外部链接
                if url.startswith("/"):
                    # 尝试找 detail-box 内的其他链接
                    external_match = re.search(
                        r"<small[^>]*><a[^>]+href=['\"]([^'\"]+)['\"]",
                        box,
                    )
                    if external_match:
                        url = external_match.group(1)

                if not url.startswith("http"):
                    continue

                results.append(
                    SearchResultItem(
                        title=title,
                        url=url,
                        thumbnail=thumbnail,
                        thumbnail_bytes=None,
                        source="Ascii2d",
                    )
                )

            except Exception as e:
                logger.debug(f"[Ascii2d] 解析单个 item 失败: {e}")

        return results
