"""图片搜索插件入口.

通过命令消息附图或回复图片消息触发搜图，返回搜索结果。
支持 aiocqhttp 平台的合并转发消息。
支持 LLM 工具调用，让 AI 帮助用户搜图。
"""

from __future__ import annotations

import json
from typing import Any

from astrbot.api import llm_tool, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import Image, Reply

from .core import image_sources, image_wait, result_sender
from .core.constant import (
    DEFAULT_ASCII2D_BOVW_MAX_RESULTS,
    DEFAULT_ASCII2D_COLOR_MAX_RESULTS,
    DEFAULT_GOOGLE_LENS_MAX_RESULTS,
    DEFAULT_SAUCENAO_MAX_RESULTS,
)
from .core.image_context import (
    get_image_context_manager,
    init_image_context_manager,
)
from .core.providers.ascii2d_strategy import Ascii2dStrategy
from .core.providers.google_lens_strategy import GoogleLensStrategy
from .core.providers.sauce_nao_strategy import SauceNaoStrategy
from .core.service import ImgExplorationService
from .core.strategy import ImageSearchStrategy
from .core.utils import (
    close_aiohttp_session,
    get_http_image_url,
    set_allow_image_upload,
    set_allow_local_file_access,
    set_proxy_url,
    set_user_agent,
)

_DEFAULT_IMAGE_WAIT_TIMEOUT_SECONDS = 60
_MIN_IMAGE_WAIT_TIMEOUT_SECONDS = 30
_MAX_IMAGE_WAIT_TIMEOUT_SECONDS = 120


class ImgExplorationPlugin(Star):
    """图片搜索插件.

    功能:
    - 命令消息附图或回复图片消息发送 "/搜图" 触发搜索
    - 无图命令可等待同一发送者随后发送图片
    - 支持 SauceNAO、Google Lens、Ascii2d 搜索引擎
    - aiocqhttp 平台使用合并转发消息展示结果
    - 其他平台使用单条消息链展示结果
    - 支持 LLM 工具调用，让 AI 帮助用户搜图
    """

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        """初始化插件."""
        super().__init__(context)
        self.config = self._config_to_dict(config)
        timeout = self._get_nested_config(
            "command",
            "image_wait_timeout_seconds",
            default=_DEFAULT_IMAGE_WAIT_TIMEOUT_SECONDS,
        )
        self._image_wait = image_wait.ImageWaitCoordinator(
            self._normalize_image_wait_timeout(timeout)
        )

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

    @staticmethod
    def _normalize_image_wait_timeout(value: Any) -> int:
        """将图片等待超时配置限制在支持范围内"""
        if isinstance(value, bool):
            return _DEFAULT_IMAGE_WAIT_TIMEOUT_SECONDS
        try:
            timeout = int(value)
        except (TypeError, ValueError):
            return _DEFAULT_IMAGE_WAIT_TIMEOUT_SECONDS
        return max(
            _MIN_IMAGE_WAIT_TIMEOUT_SECONDS,
            min(_MAX_IMAGE_WAIT_TIMEOUT_SECONDS, timeout),
        )

    @staticmethod
    def _normalize_result_limit(value: Any, default: int) -> int:
        """将搜图结果上限归一化为正整数"""
        if isinstance(value, bool):
            return default
        try:
            limit = int(value)
        except (TypeError, ValueError):
            return default
        return limit if limit > 0 else default

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
        display_config = self._get_nested_config("display", default={})
        if not isinstance(display_config, dict):
            display_config = {}

        saucenao_max_results = self._normalize_result_limit(
            display_config.get("saucenao_max_results"),
            DEFAULT_SAUCENAO_MAX_RESULTS,
        )
        google_lens_max_results = self._normalize_result_limit(
            display_config.get("google_lens_max_results"),
            DEFAULT_GOOGLE_LENS_MAX_RESULTS,
        )
        ascii2d_bovw_max_results = self._normalize_result_limit(
            display_config.get("ascii2d_bovw_max_results"),
            DEFAULT_ASCII2D_BOVW_MAX_RESULTS,
        )
        ascii2d_color_max_results = self._normalize_result_limit(
            display_config.get("ascii2d_color_max_results"),
            DEFAULT_ASCII2D_COLOR_MAX_RESULTS,
        )

        # SauceNAO
        enable_saucenao = strategies_config.get("enable_saucenao", True)
        saucenao_threshold = strategies_config.get("saucenao_similarity_threshold", 40)
        sauce_nao_key = api_keys_config.get("saucenao_api_key", "")
        if enable_saucenao and sauce_nao_key:
            self.strategies.append(
                SauceNaoStrategy(
                    api_key=sauce_nao_key,
                    similarity_threshold=saucenao_threshold,
                    max_results=saucenao_max_results,
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
            self.strategies.append(
                GoogleLensStrategy(
                    api_keys=serpapi_keys,
                    max_results=google_lens_max_results,
                )
            )
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
                    session_id=ascii2d_session_id,
                    cf_clearance=ascii2d_cf_clearance,
                    bovw_max_results=ascii2d_bovw_max_results,
                    color_max_results=ascii2d_color_max_results,
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
        await self._image_wait.close()
        # 关闭所有策略的资源
        for strategy in self.strategies:
            await strategy.close()
        # 关闭全局 aiohttp session
        await close_aiohttp_session()

    def _is_llm_tool_silent_mode(self) -> bool:
        """检查 LLM 工具是否为静默模式.

        Returns:
            True 如果静默模式开启
        """
        ai_behavior = self._get_nested_config("ai_behavior", default={})
        return ai_behavior.get("llm_tool_silent_mode", False)

    def _are_llm_tools_enabled(self) -> bool:
        """检查是否向 LLM 请求提供搜图工具"""
        return bool(
            self._get_nested_config(
                "ai_behavior",
                "enable_llm_tools",
                default=True,
            )
        )

    @staticmethod
    def _is_search_command_event(event: AstrMessageEvent) -> bool:
        """判断事件是否会作为搜图命令处理"""
        if not event.is_at_or_wake_command:
            return False
        parts = event.message_str.strip().split(maxsplit=1)
        return bool(parts) and parts[0] == "搜图"

    # ==================================================================
    # 消息监听器 - 捕获图片与消费命令等待
    # ==================================================================

    @filter.platform_adapter_type(filter.PlatformAdapterType.ALL, priority=1)
    async def on_message(self, event: AstrMessageEvent):
        """监听所有消息，捕获图片并消费命令等待"""
        messages = event.get_messages()
        image_ctx = get_image_context_manager()
        images = [comp for comp in messages if isinstance(comp, Image)]
        first_image = images[0] if images else None
        message_obj = getattr(event, "message_obj", None)
        message_id = str(getattr(message_obj, "message_id", "") or "")
        sender_id = str(event.get_sender_id() or "")
        http_sources, _ = image_sources.partition_image_sources(
            *images,
            *image_sources.get_raw_image_urls(event),
        )

        for url in http_sources:
            image_ctx.add_image(
                event,
                url,
                message_id=message_id,
                sender_id=sender_id,
            )

        is_search_command = self._is_search_command_event(event)
        if first_image is None or is_search_command:
            return

        consumption = await self._image_wait.consume(event)
        if consumption is None:
            return

        strategy_names = (
            list(consumption.strategy_names) if consumption.strategy_names else None
        )
        terminal_message = await self._run_command_search(
            event,
            first_image,
            strategy_names,
        )
        if terminal_message is not None:
            await event.send(event.plain_result(terminal_message))

        event.stop_event()

    # ==================================================================
    # LLM Tools - AI 工具函数
    # ==================================================================

    @filter.on_llm_request(priority=-1)
    async def filter_llm_tools(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        """根据配置过滤当前 LLM 请求的搜图工具"""
        if self._are_llm_tools_enabled() or req.func_tool is None:
            return

        req.func_tool.remove_tool("get_session_images")
        req.func_tool.remove_tool("search_image")

    @llm_tool("get_session_images")
    async def tool_get_session_images(self, event: AstrMessageEvent) -> str:
        """Select session images for an explicit user source-search request

        Call this tool only when the user explicitly asks to search for an image,
        find its source, or perform a reverse image search. Do not call it merely
        because an image is attached, quoted, replied to, described, identified, or
        discussed. When explicit search intent exists, call this tool before
        search_image.

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
        """Search for an image source after an explicit user request

        Call this tool only when the user explicitly asks to search for an image,
        find its source, or perform a reverse image search. Do not call it merely
        because an image is attached, quoted, replied to, described, identified, or
        discussed. Do not use it for ordinary image conversation, visual description,
        interpretation, or identification. When explicit search intent exists, call
        get_session_images first, then prefer image_id to select the target image.

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
            "[ImgExploration] AI 工具调用搜图，"
            f"选择方式: {selected_by}, "
            f"可用策略: {available_strategies}, "
            f"指定策略: {strategy_names or '全部'}"
        )
        logger.debug(f"[ImgExploration] AI 工具搜图目标 URL: {http_url}")

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
            await result_sender.send_search_results(event, result.items)

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
        """搜图指令 - 附带、回复或随后发送一张图片进行搜索

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
                image_source = await image_sources.get_image_from_reply(
                    event,
                    reply_msg,
                )
                if image_source is None:
                    yield event.plain_result("回复消息中未找到图片")
                    return

        if image_source is None:
            wait_state = await self._image_wait.create(event, strategy_names)
            if wait_state is None:
                yield event.plain_result("当前已进入搜索模式，请直接发送图片")
                return
            try:
                yield event.plain_result(
                    f"请在{self._image_wait.timeout_seconds}秒内发送图片。"
                )
                wait_result = await self._image_wait.wait(event, wait_state)
                if wait_result is image_wait.ImageWaitOutcome.TIMED_OUT:
                    yield event.plain_result("搜图等待已超时")
                    return
                if wait_result is image_wait.ImageWaitOutcome.CANCELLED:
                    return
            finally:
                await self._image_wait.clear(event, expected_state=wait_state)
            return

        await self._image_wait.clear(event)
        terminal_message = await self._run_command_search(
            event,
            image_source,
            strategy_names,
        )
        if terminal_message is not None:
            yield event.plain_result(terminal_message)

    async def _run_command_search(
        self,
        event: AstrMessageEvent,
        image_source: str | Image,
        strategy_names: list[str] | None,
    ) -> str | None:
        """执行命令搜图；成功时返回 None，否则返回用户提示"""
        await event.send(event.plain_result("搜索中..."))

        http_sources, other_sources = image_sources.partition_image_sources(
            image_source,
            *image_sources.get_raw_image_urls(event),
        )
        image_url = http_sources[0] if http_sources else None
        if image_url is None:
            for source in other_sources:
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

        await result_sender.send_search_results(event, result.items)
        return None
