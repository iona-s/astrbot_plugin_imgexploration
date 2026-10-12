"""通过 trace.moe 查找动画截图的番名、集数和时间点。"""

from __future__ import annotations

import asyncio
import math
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from astrbot.api import logger

from ..constant import (
    DEFAULT_TRACE_MOE_CUT_BORDERS,
    DEFAULT_TRACE_MOE_MAX_RESULTS,
    DEFAULT_TRACE_MOE_SIMILARITY_THRESHOLD,
    HTTP_TIMEOUT_SECONDS,
    TRACE_MOE_SEARCH_URL,
)
from ..models import ProviderSearchError, ProviderSearchOutcome, SearchResultItem
from ..strategy import ImageSearchStrategy
from ..utils import get_aiohttp_session, get_proxy_url, get_user_agent


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def _time_label(value: float) -> str:
    seconds = int(value)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return (
        f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        if hours
        else f"{minutes:02d}:{seconds:02d}"
    )


def _http_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in ("http", "https") and parsed.netloc else ""
    except ValueError:
        return ""


class TraceMoeStrategy(ImageSearchStrategy):
    """搜番策略；共享出口的免费接口要求串行请求。"""

    def __init__(
        self,
        *,
        api_key: str = "",
        similarity_threshold: int = DEFAULT_TRACE_MOE_SIMILARITY_THRESHOLD,
        max_results: int = DEFAULT_TRACE_MOE_MAX_RESULTS,
        cut_borders: bool = DEFAULT_TRACE_MOE_CUT_BORDERS,
    ) -> None:
        self.api_key = api_key.strip()
        try:
            threshold = int(similarity_threshold)
        except (TypeError, ValueError, OverflowError):
            threshold = DEFAULT_TRACE_MOE_SIMILARITY_THRESHOLD
        self.similarity_threshold = max(0, min(100, threshold))
        self.max_results = max_results
        self.cut_borders = cut_borders
        self._semaphore = asyncio.Semaphore(1)

    def get_service_name(self) -> str:
        return "trace.moe"

    async def search(self, image_url: str) -> ProviderSearchOutcome:
        if not _http_url(image_url):
            raise ProviderSearchError("trace.moe 需要 HTTP 图片 URL")
        params = {"url": image_url, "anilistInfo": "1"}
        if self.cut_borders:
            params["cutBorders"] = "1"
        headers = {"User-Agent": get_user_agent()}
        if self.api_key:
            headers["x-trace-key"] = self.api_key
        try:
            # 超时覆盖排队和网络请求；取消/超时均自动释放信号量。
            async with asyncio.timeout(HTTP_TIMEOUT_SECONDS), self._semaphore:
                session = await get_aiohttp_session()
                async with session.get(
                    TRACE_MOE_SEARCH_URL,
                    params=params,
                    headers=headers,
                    proxy=get_proxy_url(),
                    timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS),
                ) as response:
                    # 官方 402 同时表示额度耗尽和并发超限；不读取可能含图片 URL 的错误 body
                    if response.status in (402, 429):
                        raise ProviderSearchError(
                            f"trace.moe HTTP {response.status}",
                            user_notice="[trace.moe] 额度或并发受限，请稍后再试",
                        )
                    if response.status != 200:
                        raise ProviderSearchError(f"trace.moe HTTP {response.status}")
                    payload = await response.json()
            if not isinstance(payload, dict) or payload.get("error"):
                raise ProviderSearchError("trace.moe API 返回错误")
            nodes = payload.get("result")
            if not isinstance(nodes, list):
                raise ProviderSearchError("trace.moe 响应缺少结果列表")
            return self._parse_results(nodes)
        except ProviderSearchError:
            raise
        except Exception as exc:
            # 异常文本/服务端 error 可能包含图片 URL 或凭据，避免直接记录。
            logger.warning(f"[trace.moe] 搜番失败: {type(exc).__name__}")
            raise ProviderSearchError(
                f"trace.moe 搜番失败: {type(exc).__name__}"
            ) from exc

    def _parse_results(self, nodes: list[Any]) -> ProviderSearchOutcome:
        candidates = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            similarity = _number(node.get("similarity"))
            if similarity is not None and similarity <= 1:
                candidates.append((similarity, node))
        items = []
        for similarity, node in sorted(
            candidates, key=lambda pair: pair[0], reverse=True
        ):
            if similarity * 100 < self.similarity_threshold:
                continue
            anilist = node.get("anilist")
            info = anilist if isinstance(anilist, dict) else {}
            anime_id = info.get("id", anilist)
            valid_id = (
                isinstance(anime_id, int)
                and not isinstance(anime_id, bool)
                and anime_id > 0
            )
            titles = info.get("title")
            titles = titles if isinstance(titles, dict) else {}
            title = next(
                (
                    titles[k]
                    for k in ("native", "romaji", "english")
                    if isinstance(titles.get(k), str) and titles[k].strip()
                ),
                None,
            )
            if not title:
                filename = node.get("filename")
                title = (
                    filename if isinstance(filename, str) and filename else "未知番名"
                )
            details = []
            start = _number(node.get("episode_start"))
            end = _number(node.get("episode_end"))
            episode = node.get("episode")
            # 多集文件的 episode 可能是 [起始集, 结束集]；仅接受全为正数的数组
            if (start is None or end is None) and isinstance(episode, list) and episode:
                numbers = [_number(value) for value in episode]
                if all(number is not None and number > 0 for number in numbers):
                    start, end = numbers[0], numbers[-1]
            if start is not None and end is not None and 0 < start <= end:
                details.append(
                    f"第{start:g}集"
                    if start == end
                    else f"第{start:g}–{end:g}集（文件覆盖多集）"
                )
            else:
                if isinstance(episode, str) and episode.strip():
                    details.append(f"集数: {episode}")
                elif (
                    episode_number := _number(episode)
                ) is not None and episode_number > 0:
                    details.append(f"第{episode_number:g}集")
                else:
                    details.append("集数未知（可能为 OVA、特典或未标注）")
            frame_time = _number(node.get("at"))
            from_time = _number(node.get("from"))
            to_time = _number(node.get("to"))
            if frame_time is None:
                frame_time = from_time
            details.append(
                f"文件内时间点: {_time_label(frame_time)}"
                if frame_time is not None
                else "时间点未知"
            )
            if from_time is not None and to_time is not None and to_time >= from_time:
                details.append(
                    f"匹配片段: {_time_label(from_time)}–{_time_label(to_time)}"
                )
            if similarity < 0.9:
                details.append("低置信度候选，请核对截图")
            is_adult = info.get("isAdult") is True
            if is_adult:
                details.append("成人内容标记，已隐藏预览")
            items.append(
                SearchResultItem(
                    title=title,
                    url=f"https://anilist.co/anime/{anime_id}" if valid_id else "",
                    thumbnail="" if is_adult else _http_url(node.get("image")),
                    source=self.get_service_name(),
                    similarity=f"{similarity * 100:.2f}%",
                    description="\n".join(details),
                )
            )
            if len(items) >= self.max_results:
                break
        notices = []
        if nodes and not items:
            notices.append(
                f"[trace.moe] 未找到达到 {self.similarity_threshold}% 相似度的可靠搜番结果"
            )
        return ProviderSearchOutcome(items=items, user_notices=notices)
