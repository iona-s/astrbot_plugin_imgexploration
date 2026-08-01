"""图片来源提取与选择"""

from __future__ import annotations

from collections.abc import Mapping

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.message.components import Image, Reply

from .utils import get_bot_api


def as_http_image_url(value: object) -> str | None:
    """将 HTTP(S) 图片字段规范为可用候选"""
    if not isinstance(value, str):
        return None
    if not value.lower().startswith(("http://", "https://")):
        return None
    return value


def get_raw_image_urls(event: AstrMessageEvent) -> list[str]:
    """从结构化原始事件提取图片 URL"""
    message_obj = getattr(event, "message_obj", None)
    raw_message = getattr(message_obj, "raw_message", None)
    if isinstance(raw_message, Mapping):
        segments = raw_message.get("message")
    else:
        segments = getattr(raw_message, "message", None)
    if not isinstance(segments, (list, tuple)):
        return []

    urls: list[str] = []
    for segment in segments:
        if not isinstance(segment, Mapping) or segment.get("type") != "image":
            continue
        data = segment.get("data")
        if not isinstance(data, Mapping):
            continue
        url = as_http_image_url(data.get("url"))
        if url is not None:
            urls.append(url)
    return urls


def partition_image_sources(
    *image_sources: str | Image | None,
) -> tuple[list[str], list[str]]:
    """展开并去重图片来源，分别返回 HTTP(S) 与其他候选"""
    http_sources: list[str] = []
    other_sources: list[str] = []
    seen: set[str] = set()

    for image_source in image_sources:
        if isinstance(image_source, Image):
            values = (image_source.url, image_source.file)
        elif isinstance(image_source, str):
            values = (image_source,)
        else:
            continue

        for value in values:
            if not isinstance(value, str) or not value or value in seen:
                continue
            seen.add(value)
            if as_http_image_url(value) is not None:
                http_sources.append(value)
            else:
                other_sources.append(value)

    return http_sources, other_sources


async def get_image_from_reply(
    event: AstrMessageEvent,
    reply: Reply,
) -> Image | None:
    """从回复消息中提取第一张图片

    Args:
        event: 消息事件
        reply: 回复组件

    Returns:
        图片组件，失败返回 None
    """
    reply_image = next(
        (comp for comp in reply.chain or [] if isinstance(comp, Image)),
        None,
    )
    reply_http_sources, _ = partition_image_sources(reply_image)
    if reply_http_sources:
        return reply_image

    # 尝试通过 bot API 获取原消息
    bot = get_bot_api(event)
    if bot:
        try:
            # 获取原消息内容
            msg_resp = await bot.call_action("get_msg", message_id=int(reply.id))
            if isinstance(msg_resp, Mapping):
                message = msg_resp.get("message")
                # 解析消息中的图片
                for seg in message if isinstance(message, list) else []:
                    if not isinstance(seg, Mapping) or seg.get("type") != "image":
                        continue
                    data = seg.get("data")
                    if not isinstance(data, Mapping):
                        continue
                    url = data.get("url")
                    file = data.get("file")
                    if not isinstance(url, str):
                        url = None
                    if not isinstance(file, str):
                        file = None
                    if url or file:
                        onebot_image = Image(file=file, url=url)
                        onebot_http_sources, _ = partition_image_sources(onebot_image)
                        if onebot_http_sources:
                            return onebot_image
                        return reply_image if reply_image is not None else onebot_image
        except Exception as e:
            logger.debug(f"[ImgExploration] 获取回复消息失败: {e}")

    return reply_image
