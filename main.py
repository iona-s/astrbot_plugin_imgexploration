"""图片搜索插件入口.

通过命令消息附图或回复图片消息触发搜图，返回搜索结果。
支持 aiocqhttp 平台的合并转发消息。
支持 LLM 工具调用，让 AI 帮助用户搜图。
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any

from astrbot.api import llm_tool, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import Image, Node, Nodes, Plain, Reply

from .ascii2d_strategy import Ascii2dStrategy
from .constant import LLM_TOOLS
from .google_lens_strategy import GoogleLensStrategy
from .image_context import (
    get_image_context_manager,
    init_image_context_manager,
)
from .models import SearchResultItem
from .sauce_nao_strategy import SauceNaoStrategy
from .service import ImgExplorationService
from .strategy import ImageSearchStrategy
from .utils import (
    close_aiohttp_session,
    get_bot_api,
    get_http_image_url,
    is_aiocqhttp_platform,
    set_allow_image_upload,
    set_allow_local_file_access,
    set_proxy_url,
    set_user_agent,
)


class ImgExplorationPlugin(Star):
    """图片搜索插件.

    功能:
    - 命令消息附图或回复图片消息发送 "/搜图" 触发搜索
    - 支持 SauceNAO、Google Lens、Ascii2d 搜索引擎
    - aiocqhttp 平台使用合并转发消息展示结果
    - 其他平台使用单条消息链展示结果
    - 支持 LLM 工具调用，让 AI 帮助用户搜图
    """

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        """初始化插件."""
        super().__init__(context)
        self.config = self._config_to_dict(config)

        # 初始化搜图策略
        self.strategies: list[ImageSearchStrategy] = []
        self._init_strategies()

        # 初始化搜索服务
        self.service = ImgExplorationService(self.strategies)

    @staticmethod
    def _config_to_dict(config: AstrBotConfig) -> dict:
        """将 AstrBotConfig 转换为普通 dict.

        AstrBotConfig 可能是 dict 子类或具有 __iter__ 的映射对象。
        """
        if isinstance(config, dict):
            return dict(config)
        # 尝试将其作为映射对象处理
        try:
            return dict(config.items())  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            # 最后尝试遍历键值
            try:
                return {k: config[k] for k in config}  # type: ignore[index, iter]
            except Exception:
                logger.warning("[ImgExploration] 无法解析配置对象，使用空配置")
                return {}

    def _get_nested_config(self, *keys: str, default: Any = None) -> Any:
        """获取嵌套配置值.

        Args:
            *keys: 嵌套的配置键路径
            default: 默认值

        Returns:
            配置值
        """
        value = self.config
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return default
            if value is None:
                return default
        return value

    def _init_strategies(self) -> None:
        """初始化搜图策略."""
        # 设置网络配置
        network_config = self._get_nested_config("network", default={})
        proxy_url = network_config.get("proxy_url", "")
        set_proxy_url(proxy_url)
        user_agent = network_config.get("user_agent", "")
        set_user_agent(user_agent)
        allow_image_upload = network_config.get("allow_image_upload", True)
        set_allow_image_upload(allow_image_upload)
        allow_local_file_access = network_config.get("allow_local_file_access", False)
        set_allow_local_file_access(allow_local_file_access)

        # 初始化图片上下文管理器
        ai_behavior = self._get_nested_config("ai_behavior", default={})
        isolation_mode = ai_behavior.get("image_context_isolation", "session")
        max_images = ai_behavior.get("max_images_per_session", 20)
        image_ttl_seconds = ai_behavior.get("image_context_ttl_seconds", 0)
        max_sessions = ai_behavior.get("max_image_context_sessions", 200)
        include_url_in_context = ai_behavior.get("include_image_url_in_context", True)
        init_image_context_manager(
            isolation_mode=isolation_mode,
            max_images=max_images,
            ttl_seconds=image_ttl_seconds,
            max_sessions=max_sessions,
            include_url_in_context=include_url_in_context,
        )

        # 获取策略启用配置
        strategies_config = self._get_nested_config("strategies", default={})
        api_keys_config = self._get_nested_config("api_keys", default={})

        # SauceNAO
        enable_saucenao = strategies_config.get("enable_saucenao", True)
        saucenao_threshold = strategies_config.get("saucenao_similarity_threshold", 40)
        sauce_nao_key = api_keys_config.get("saucenao_api_key", "")
        if enable_saucenao and sauce_nao_key:
            self.strategies.append(
                SauceNaoStrategy(
                    api_key=sauce_nao_key, similarity_threshold=saucenao_threshold
                )
            )
            logger.info(
                f"[ImgExploration] 已加载 SauceNAO 策略 (相似度阈值: {saucenao_threshold}%)"
            )
        elif enable_saucenao:
            logger.warning("[ImgExploration] SauceNAO API Key 未配置，跳过该策略")
        else:
            logger.info("[ImgExploration] SauceNAO 策略已禁用")

        # Google Lens (SerpAPI)
        enable_google_lens = strategies_config.get("enable_google_lens", True)
        serpapi_keys = api_keys_config.get("serpapi_keys", [])
        if enable_google_lens and serpapi_keys and isinstance(serpapi_keys, list):
            self.strategies.append(GoogleLensStrategy(api_keys=serpapi_keys))
            logger.info("[ImgExploration] 已加载 Google Lens 策略")
        elif enable_google_lens:
            logger.warning(
                "[ImgExploration] SerpAPI Keys 未配置，跳过 Google Lens 策略"
            )
        else:
            logger.info("[ImgExploration] Google Lens 策略已禁用")

        # Ascii2d
        enable_ascii2d = strategies_config.get("enable_ascii2d", True)
        ascii2d_session_id = api_keys_config.get("ascii2d_session_id", "")
        ascii2d_cf_clearance = api_keys_config.get("ascii2d_cf_clearance", "")
        if enable_ascii2d and ascii2d_session_id:
            self.strategies.append(
                Ascii2dStrategy(
                    session_id=ascii2d_session_id, cf_clearance=ascii2d_cf_clearance
                )
            )
            logger.info("[ImgExploration] 已加载 Ascii2d 策略")
        elif enable_ascii2d:
            logger.warning("[ImgExploration] Ascii2d session_id 未配置，跳过该策略")
        else:
            logger.info("[ImgExploration] Ascii2d 策略已禁用")

        logger.info(f"[ImgExploration] 共加载 {len(self.strategies)} 个搜图策略")

        if len(self.strategies) == 0:
            logger.error("[ImgExploration] 没有可用的搜图策略！请检查配置。")

    async def terminate(self):
        """插件卸载时清理资源."""
        # 注销 LLM 工具
        self._unregister_llm_tools()
        # 关闭所有策略的资源
        for strategy in self.strategies:
            await strategy.close()
        # 关闭全局 aiohttp session
        await close_aiohttp_session()

    def _unregister_llm_tools(self) -> None:
        """注销 LLM 工具函数."""
        try:
            func_tool_mgr = self.context.get_llm_tool_manager()
            for tool_name in LLM_TOOLS:
                func_tool_mgr.remove_tool(tool_name)
                logger.info(f"[ImgExploration] 已移除 LLM 工具: {tool_name}")
        except Exception as e:
            logger.error(f"[ImgExploration] 移除 LLM 工具失败: {e}")

    def _is_llm_tool_silent_mode(self) -> bool:
        """检查 LLM 工具是否为静默模式.

        Returns:
            True 如果静默模式开启
        """
        ai_behavior = self._get_nested_config("ai_behavior", default={})
        return ai_behavior.get("llm_tool_silent_mode", False)

    # ==================================================================
    # 消息监听器 - 捕获图片到上下文
    # ==================================================================

    @filter.platform_adapter_type(filter.PlatformAdapterType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """监听所有消息，捕获图片到上下文."""
        messages = event.get_messages()
        image_ctx = get_image_context_manager()

        for comp in messages:
            if isinstance(comp, Image):
                # 获取图片 URL
                url = comp.url or comp.file
                if url and url.startswith(("http://", "https://")):
                    image_ctx.add_image(
                        event,
                        url,
                        message_id=str(getattr(event, "message_id", "")),
                        sender_id=str(getattr(event, "user_id", "")),
                    )
                    logger.debug(f"[ImgExploration] 捕获图片到上下文: {url[:50]}...")

    # ==================================================================
    # LLM Tools - AI 工具函数
    # ==================================================================

    @llm_tool("get_session_images")
    async def tool_get_session_images(self, event: AstrMessageEvent) -> str:
        """Get images available in the current session before calling search_image.

        Returns:
            JSON result containing image_id, image_index, and optional metadata for selection.
        """
        image_ctx = get_image_context_manager()
        info = image_ctx.get_image_context_info(event)
        return json.dumps(info, ensure_ascii=False)

    @llm_tool("search_image")
    async def tool_search_image(
        self,
        event: AstrMessageEvent,
        image_index: int = -1,
        strategies: str | None = None,
        image_id: str | None = None,
    ) -> str:
        """Search for the source of an image.

        BEFORE calling this tool, call get_session_images and prefer image_id to select the target image.

        Args:
            image_index(int): Fallback image index. -1 = most recent image, 1 = first/oldest image.
            strategies(string): Optional. Comma-separated strategy list: saucenao/sauce, google, ascii2d/2d.
            image_id(string): Optional stable image ID returned by get_session_images. Higher priority than image_index.

        Returns:
            JSON result with search results. You MUST present the results to the user with URLs and titles.
        """
        # 检查是否有可用策略
        if not self.strategies:
            return json.dumps(
                {"success": False, "error": "没有可用的搜图 API，请检查配置"},
                ensure_ascii=False,
            )

        image_ctx = get_image_context_manager()
        image_url = None
        selected_by = "image_index"

        # 优先使用稳定 image_id，兼容旧调用时回退到 image_index。
        if image_id and image_id.strip():
            image_url = image_ctx.get_image_by_id(event, image_id.strip())
            selected_by = "image_id"
        if not image_url:
            image_url = image_ctx.get_image_by_index(event, image_index)
            selected_by = "image_index"

        if not image_url:
            images_info = image_ctx.get_image_context_info(event)
            return json.dumps(
                {
                    "success": False,
                    "error": "未找到指定的图片",
                    "image_context": images_info,
                    "hint": "请先让用户发送图片，或先调用 get_session_images 后使用 image_id / image_index 选择图片",
                },
                ensure_ascii=False,
            )

        # 转换为 HTTP URL
        http_url = await get_http_image_url(image_url)
        if not http_url:
            return json.dumps(
                {
                    "success": False,
                    "error": "无法获取有效的图片 URL",
                    "hint": "请确保图片可访问，或让用户回复图片发送「搜图」命令",
                },
                ensure_ascii=False,
            )

        # 解析策略参数
        strategy_names = None
        if strategies and strategies.strip():
            strategy_names = [s.strip() for s in strategies.split(",") if s.strip()]

        available_strategies = self.service.get_available_strategies()

        # 验证策略是否存在
        if strategy_names:
            _, not_found = self.service.resolve_strategy_names(strategy_names)
            if not_found:
                return json.dumps(
                    {
                        "success": False,
                        "error": f"以下策略不可用: {', '.join(not_found)}",
                        "available_strategies": available_strategies,
                    },
                    ensure_ascii=False,
                )

        logger.info(
            f"[ImgExploration] AI 工具调用搜图: {http_url}, "
            f"选择方式: {selected_by}, "
            f"可用策略: {available_strategies}, "
            f"指定策略: {strategy_names or '全部'}"
        )

        # 执行搜索
        result = await self.service.explore(http_url, strategy_names=strategy_names)

        if not result.items:
            return json.dumps(
                {"success": False, "error": "未找到相关图片来源"}, ensure_ascii=False
            )

        # 检查是否为静默模式
        silent_mode = self._is_llm_tool_silent_mode()

        # 非静默模式下，像命令方式一样发送消息给用户
        if not silent_mode:
            await self._send_search_results(event, result.items)

        # 构建结果供 AI 参考
        items_data = []
        for idx, item in enumerate(result.items, start=1):
            items_data.append(
                {
                    "index": idx,
                    "title": item.title,
                    "url": item.url,
                    "source": item.source,
                    "similarity": item.similarity,
                    "domain": item.domain,
                }
            )

        # 根据模式构建不同的指令
        result_count = len(result.items)
        if silent_mode:
            instruction = (
                f"搜索结果如下，请向用户展示：\n"
                f"找到 {result_count} 个结果：\n"
                "1. 标题 - 来源: xxx, 相似度: xx%\n"
                "   链接: URL\n"
                "2. ...\n"
                "注意：请直接输出纯文本，不要使用 Markdown 链接语法 [文本](URL)，"
                "因为部分平台不支持 Markdown。请直接输出完整 URL。"
            )
        else:
            instruction = (
                f"搜索结果已以图片消息形式发送给用户。你仍需要向用户说明搜索结果：\n"
                f"找到 {result_count} 个结果：\n"
                "1. 标题 - 来源: xxx, 相似度: xx%\n"
                "   链接: URL\n"
                "2. ...\n"
                "注意：请直接输出纯文本，不要使用 Markdown 链接语法 [文本](URL)，"
                "因为部分平台不支持 Markdown。请直接输出完整 URL。"
            )

        return json.dumps(
            {
                "success": True,
                "count": len(result.items),
                "items": items_data,
                "available_strategies": available_strategies,
                "used_strategies": strategy_names
                if strategy_names
                else available_strategies,
                "selected_by": selected_by,
                "message_sent": not silent_mode,
                "instruction": instruction,
            },
            ensure_ascii=False,
        )

    # ==================================================================
    # Command Handlers
    # ==================================================================

    @filter.command("搜图")
    async def search_image_cmd(self, event: AstrMessageEvent):
        """搜图指令 - 附带或回复一张图片进行搜索.

        用法:
        - 搜图 (无参数): 使用所有可用策略搜索
        - 搜图 saucenao: 只使用 SauceNAO 搜索
        - 搜图 google: 只使用 Google Lens 搜索
        - 搜图 ascii2d: 只使用 Ascii2d 搜索
        - 搜图 saucenao,google: 使用多个指定策略

        别名: sauce=saucenao, 2d=ascii2d
        """
        # 检查是否有可用策略
        if not self.strategies:
            yield event.plain_result(
                "没有可用的搜图 API，请检查配置。\n"
                "需要在 WebUI 中配置至少一个搜图引擎的 API Key。"
            )
            return

        # 解析命令参数
        message_str = event.message_str.strip()
        # 使用空格分割，移除命令本身，获取剩余参数
        parts = message_str.split(maxsplit=1)
        args_str = parts[1] if len(parts) > 1 else ""

        # 解析策略参数
        strategy_names = None
        if args_str:
            strategy_names = [s.strip() for s in args_str.split(",") if s.strip()]

        available_strategies = self.service.get_available_strategies()

        # 验证策略是否存在
        if strategy_names:
            _, not_found = self.service.resolve_strategy_names(strategy_names)
            if not_found:
                yield event.plain_result(
                    f"以下策略不可用: {', '.join(not_found)}\n"
                    f"当前可用策略: {', '.join(available_strategies)}"
                )
                return

        # 优先使用当前消息的图片，如果没有再检查回复消息
        messages = event.get_messages()
        image_source: str | Image | None = next(
            (comp for comp in messages if isinstance(comp, Image)),
            None,
        )

        if image_source is None:
            reply_msg = next(
                (comp for comp in messages if isinstance(comp, Reply)),
                None,
            )
            if reply_msg is not None:
                image_source = await self._get_image_from_reply(event, reply_msg)

        if image_source is None:
            yield event.plain_result("请在命令中附带图片，或回复一张图片进行搜图")
            return

        terminal_message = await self._run_command_search(
            event,
            image_source,
            strategy_names,
        )
        if terminal_message is not None:
            yield event.plain_result(terminal_message)

    @staticmethod
    def _get_image_source_candidates(image_source: str | Image) -> list[str]:
        """按 HTTP(S) 优先级提取图片来源候选。"""
        if isinstance(image_source, str):
            values = [image_source]
        else:
            values = [image_source.url, image_source.file]

        http_sources: list[str] = []
        other_sources: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str) or not value or value in seen:
                continue

            seen.add(value)
            if value.lower().startswith(("http://", "https://")):
                http_sources.append(value)
            else:
                other_sources.append(value)

        return [*http_sources, *other_sources]

    async def _run_command_search(
        self,
        event: AstrMessageEvent,
        image_source: str | Image,
        strategy_names: list[str] | None,
    ) -> str | None:
        """执行命令搜图；成功时返回 None，否则返回用户提示。"""
        await event.send(event.plain_result("搜索中..."))

        candidates = self._get_image_source_candidates(image_source)
        image_url = next(
            (
                source
                for source in candidates
                if source.lower().startswith(("http://", "https://"))
            ),
            None,
        )
        if image_url is None:
            for source in candidates:
                image_url = await get_http_image_url(source)
                if image_url:
                    break

        if not image_url:
            return "获取图片失败"

        logger.info(
            f"[ImgExploration] 收到命令搜图请求，策略: {strategy_names or '全部'}"
        )
        result = await self.service.explore(
            image_url,
            strategy_names=strategy_names,
        )

        if not result.items:
            return "未找到相关图片来源，请尝试更换图片或稍后重试。"

        await self._send_search_results(event, result.items)
        return None

    @staticmethod
    async def _get_image_from_reply(
        event: AstrMessageEvent, reply: Reply
    ) -> Image | None:
        """从回复消息中提取第一张图片。

        Args:
            event: 消息事件
            reply: 回复组件

        Returns:
            图片组件，失败返回 None
        """
        for comp in reply.chain or []:
            if isinstance(comp, Image):
                return comp

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
                            return Image(file=file, url=url)
            except Exception as e:
                logger.debug(f"[ImgExploration] 获取回复消息失败: {e}")

        return None

    async def _send_search_results(
        self, event: AstrMessageEvent, items: list[SearchResultItem]
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
                await self._send_forward_msg(event, items)
                return
            except Exception as e:
                logger.warning(
                    f"[ImgExploration] 合并转发发送失败，降级为普通消息: {e}"
                )

        try:
            await self._send_normal_msg(event, items)
        except Exception as e:
            logger.error(f"[ImgExploration] 普通消息发送失败，降级为纯文本: {e}")
            await self._send_plain_text_msg(event, items)

    @staticmethod
    def _build_forward_content(
        idx: int, item: SearchResultItem, include_image: bool = True
    ) -> list[Any]:
        """构建单个转发节点内容，支持无图降级重试."""
        content: list[Any] = []

        content.append(Plain(f"{idx}. {item.title}"))

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

    @staticmethod
    def _is_suspicious_forward_image(item: SearchResultItem) -> bool:
        """判定转发中可能触发失败的图片来源，优先处理 Ascii2d 结果."""
        source = (item.source or "").strip().lower()
        return source in {"ascii2d", "ascii2d search", "2d"} or "ascii2d" in source

    @staticmethod
    async def _send_forward_msg(
        event: AstrMessageEvent, items: list[SearchResultItem]
    ) -> None:
        """使用合并转发消息发送结果 (aiocqhttp 平台).

        Args:
            event: 消息事件
            items: 搜索结果列表
        """
        nodes: list[Node] = []

        for idx, item in enumerate(items, start=1):
            node = Node(
                name="搜图助手",
                uin=str(event.get_self_id() or "0"),
                content=ImgExplorationPlugin._build_forward_content(idx, item),
            )
            nodes.append(node)

        if not nodes:
            return

        try:
            forward_msg = Nodes(nodes=nodes)
            await event.send(event.chain_result([forward_msg]))
        except Exception as e:
            logger.warning(
                f"[ImgExploration] 转发消息发送失败，尝试仅移除可疑图片重试: {e}"
            )

            # 仅剔除可疑来源图片（例如 Ascii2d），尽量保留其他策略缩略图。
            retry_nodes: list[Node] = []
            removed_count = 0
            for idx, item in enumerate(items, start=1):
                drop_image = ImgExplorationPlugin._is_suspicious_forward_image(item)
                if drop_image and (item.thumbnail_bytes or item.thumbnail):
                    removed_count += 1
                retry_nodes.append(
                    Node(
                        name="搜图助手",
                        uin=str(event.get_self_id() or "0"),
                        content=ImgExplorationPlugin._build_forward_content(
                            idx, item, include_image=not drop_image
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
            forward_msg = Nodes(nodes=retry_nodes)
            await event.send(event.chain_result([forward_msg]))

    @staticmethod
    async def _send_normal_msg(
        event: AstrMessageEvent, items: list[SearchResultItem]
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

            # 分隔线
            chain.append(Plain("---\n"))

        # 发送消息链
        await event.send(event.chain_result(chain))

    @staticmethod
    async def _send_plain_text_msg(
        event: AstrMessageEvent, items: list[SearchResultItem]
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
