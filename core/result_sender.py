"""搜索结果消息构造与发送降级"""

from __future__ import annotations

import base64
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.message.components import Image, Node, Nodes, Plain

from .models import SearchResultItem
from .utils import is_aiocqhttp_platform


async def send_search_results(
    event: AstrMessageEvent,
    items: list[SearchResultItem],
) -> None:
    """发送搜索结果.

    根据平台选择发送方式:
    - aiocqhttp: 使用合并转发消息
    - 其他平台: 使用单条消息链

    Args:
        event: 消息事件
        items: 搜索结果列表
    """
    if is_aiocqhttp_platform(event):
        try:
            await send_forward_message(event, items)
            return
        except Exception as e:
            logger.warning(f"[ImgExploration] 合并转发发送失败，降级为普通消息: {e}")

    try:
        await send_normal_message(event, items)
    except Exception as e:
        logger.error(f"[ImgExploration] 普通消息发送失败，降级为纯文本: {e}")
        await send_plain_text_message(event, items)


def build_forward_content(
    idx: int,
    item: SearchResultItem,
    include_image: bool = True,
) -> list[Any]:
    """构建单个转发节点内容，支持无图降级重试"""
    content: list[Any] = [Plain(f"{idx}. {item.title}")]

    if item.source:
        content.append(Plain(f"\n来源: {item.source}"))
    if item.similarity:
        content.append(Plain(f" | 相似度: {item.similarity}"))
    if item.domain:
        content.append(Plain(f"\n域名: {item.domain}"))

    if include_image:
        if item.thumbnail_bytes:
            b64 = base64.b64encode(item.thumbnail_bytes).decode("ascii")
            content.append(Image(file=f"base64://{b64}"))
        elif item.thumbnail:
            content.append(Image(file=item.thumbnail))

    content.append(Plain(f"\n链接: {item.url}"))
    return content


def _is_suspicious_forward_image(item: SearchResultItem) -> bool:
    """判定转发中可能触发失败的图片来源，优先处理 Ascii2d 结果"""
    source = (item.source or "").strip().lower()
    return source in {"ascii2d", "ascii2d search", "2d"} or "ascii2d" in source


async def send_forward_message(
    event: AstrMessageEvent,
    items: list[SearchResultItem],
) -> None:
    """使用合并转发消息发送结果 (aiocqhttp 平台).

    Args:
        event: 消息事件
        items: 搜索结果列表
    """
    nodes = [
        Node(
            name="搜图助手",
            uin=str(event.get_self_id() or "0"),
            content=build_forward_content(idx, item),
        )
        for idx, item in enumerate(items, start=1)
    ]
    if not nodes:
        return

    try:
        await event.send(event.chain_result([Nodes(nodes=nodes)]))
    except Exception as e:
        logger.warning(
            f"[ImgExploration] 转发消息发送失败，尝试仅移除可疑图片重试: {e}"
        )

        # 仅剔除可疑来源图片（例如 Ascii2d），尽量保留其他策略缩略图。
        retry_nodes: list[Node] = []
        removed_count = 0
        for idx, item in enumerate(items, start=1):
            drop_image = _is_suspicious_forward_image(item)
            if drop_image and (item.thumbnail_bytes or item.thumbnail):
                removed_count += 1
            retry_nodes.append(
                Node(
                    name="搜图助手",
                    uin=str(event.get_self_id() or "0"),
                    content=build_forward_content(
                        idx,
                        item,
                        include_image=not drop_image,
                    ),
                )
            )

        if removed_count == 0:
            logger.warning(
                "[ImgExploration] 未识别到可疑图片来源，保持原异常交由上层降级"
            )
            raise

        logger.info(
            f"[ImgExploration] 已移除 {removed_count} 张可疑来源图片，保留其他图片后重试转发"
        )
        await event.send(event.chain_result([Nodes(nodes=retry_nodes)]))


async def send_normal_message(
    event: AstrMessageEvent,
    items: list[SearchResultItem],
) -> None:
    """使用单条消息链发送结果 (非 aiocqhttp 平台).

    Args:
        event: 消息事件
        items: 搜索结果列表
    """
    # 构建消息链
    chain: list[Any] = []
    for idx, item in enumerate(items, start=1):
        # 标题
        chain.append(Plain(f"{idx}. {item.title}\n"))

        # 来源和相似度
        info_parts = []
        if item.source:
            info_parts.append(f"来源: {item.source}")
        if item.similarity:
            info_parts.append(f"相似度: {item.similarity}")
        if info_parts:
            chain.append(Plain(f"{' | '.join(info_parts)}\n"))

        # 缩略图
        if item.thumbnail_bytes:
            b64 = base64.b64encode(item.thumbnail_bytes).decode("ascii")
            chain.append(Image(file=f"base64://{b64}"))
        elif item.thumbnail:
            chain.append(Image(file=item.thumbnail))
        # 链接
        chain.append(Plain(f"\n链接: {item.url}\n"))
        chain.append(Plain("---\n"))

    await event.send(event.chain_result(chain))


async def send_plain_text_message(
    event: AstrMessageEvent,
    items: list[SearchResultItem],
) -> None:
    """发送纯文本兜底结果，确保在消息组件失败时仍能回复用户."""
    lines: list[str] = []
    for idx, item in enumerate(items, start=1):
        lines.append(f"{idx}. {item.title}")
        info_parts = []
        if item.source:
            info_parts.append(f"来源: {item.source}")
        if item.similarity:
            info_parts.append(f"相似度: {item.similarity}")
        if item.domain:
            info_parts.append(f"域名: {item.domain}")
        if info_parts:
            lines.append(" | ".join(info_parts))
        lines.append(f"链接: {item.url}")
        lines.append("---")

    await event.send(event.plain_result("\n".join(lines).rstrip("-\n")))
