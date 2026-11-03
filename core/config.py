"""插件配置的读取、归一化、旧配置迁移和搜图策略装配."""

from __future__ import annotations

from typing import Any

from astrbot.api import logger
from astrbot.core import AstrBotConfig

from .constant import (
    DEFAULT_ALLOW_IMAGE_UPLOAD,
    DEFAULT_ALLOW_LOCAL_FILE_ACCESS,
    DEFAULT_ASCII2D_BOVW_MAX_RESULTS,
    DEFAULT_ASCII2D_COLOR_MAX_RESULTS,
    DEFAULT_ENABLE_TRACE_MOE,
    DEFAULT_GOOGLE_LENS_AUTO_CROP,
    DEFAULT_GOOGLE_LENS_COUNTRY,
    DEFAULT_GOOGLE_LENS_LANGUAGE,
    DEFAULT_GOOGLE_LENS_MAX_RESULTS,
    DEFAULT_GOOGLE_LENS_NO_CACHE,
    DEFAULT_GOOGLE_LENS_SAFE_SEARCH,
    DEFAULT_GOOGLE_LENS_SEARCH_TYPE,
    DEFAULT_IMAGE_CONTEXT_ISOLATION,
    DEFAULT_IMAGE_CONTEXT_TTL_SECONDS,
    DEFAULT_MAX_IMAGE_CONTEXT_SESSIONS,
    DEFAULT_MAX_IMAGES_PER_SESSION,
    DEFAULT_SAUCENAO_MAX_RESULTS,
    DEFAULT_SAUCENAO_SIMILARITY_THRESHOLD,
    DEFAULT_TRACE_MOE_CUT_BORDERS,
    DEFAULT_TRACE_MOE_MAX_RESULTS,
    DEFAULT_TRACE_MOE_SIMILARITY_THRESHOLD,
)
from .image_context import init_image_context_manager
from .providers.ascii2d_strategy import Ascii2dStrategy
from .providers.google_lens_strategy import GoogleLensStrategy
from .providers.sauce_nao_strategy import SauceNaoStrategy
from .providers.trace_moe_strategy import TraceMoeStrategy
from .strategy import ImageSearchStrategy
from .utils import set_allow_image_upload, set_allow_local_file_access, set_proxy_url

DEFAULT_IMAGE_WAIT_TIMEOUT_SECONDS = 60
MIN_IMAGE_WAIT_TIMEOUT_SECONDS = 30
MAX_IMAGE_WAIT_TIMEOUT_SECONDS = 120
LLM_RESPONSE_RESULTS_ONLY = "results_only"
LLM_RESPONSE_RESULTS_WITH_SUMMARY = "results_with_summary"
LLM_RESPONSE_LLM_ONLY = "llm_only"
LLM_RESPONSE_MODES = {
    LLM_RESPONSE_RESULTS_ONLY,
    LLM_RESPONSE_RESULTS_WITH_SUMMARY,
    LLM_RESPONSE_LLM_ONLY,
}
LEGACY_CONFIG_MIGRATED = ""
# 配置版本 2 起按搜图引擎分组；旧分组保留在配置结构中以便迁移和回退
ENGINE_CONFIG_VERSION = 2
LEGACY_ENGINE_CONFIG_KEYS = {
    ("strategies", "enable_saucenao"): ("saucenao", "enable"),
    ("api_keys", "saucenao_api_key"): ("saucenao", "api_key"),
    ("strategies", "saucenao_similarity_threshold"): (
        "saucenao",
        "similarity_threshold",
    ),
    ("display", "saucenao_max_results"): ("saucenao", "max_results"),
    ("strategies", "enable_google_lens"): ("google_lens", "enable"),
    ("api_keys", "serpapi_keys"): ("google_lens", "serpapi_keys"),
    ("strategies", "google_lens_search_type"): ("google_lens", "search_type"),
    ("strategies", "google_lens_language"): ("google_lens", "language"),
    ("strategies", "google_lens_country"): ("google_lens", "country"),
    ("strategies", "google_lens_safe_search"): ("google_lens", "safe_search"),
    ("strategies", "google_lens_auto_crop"): ("google_lens", "auto_crop"),
    ("strategies", "google_lens_no_cache"): ("google_lens", "no_cache"),
    ("display", "google_lens_max_results"): ("google_lens", "max_results"),
    ("strategies", "enable_ascii2d"): ("ascii2d", "enable"),
    ("api_keys", "ascii2d_session_id"): ("ascii2d", "session_id"),
    ("api_keys", "ascii2d_cf_clearance"): ("ascii2d", "cf_clearance"),
    ("display", "ascii2d_bovw_max_results"): ("ascii2d", "bovw_max_results"),
    ("display", "ascii2d_color_max_results"): ("ascii2d", "color_max_results"),
    ("strategies", "enable_trace_moe"): ("trace_moe", "enable"),
    ("strategies", "trace_moe_include_in_default"): (
        "trace_moe",
        "include_in_default",
    ),
    ("api_keys", "trace_moe_api_key"): ("trace_moe", "api_key"),
    ("strategies", "trace_moe_similarity_threshold"): (
        "trace_moe",
        "similarity_threshold",
    ),
    ("strategies", "trace_moe_cut_borders"): ("trace_moe", "cut_borders"),
    ("display", "trace_moe_max_results"): ("trace_moe", "max_results"),
}


def config_to_dict(config: AstrBotConfig) -> dict:
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


def get_nested_config(config: dict, *keys: str, default: Any = None) -> Any:
    """获取嵌套配置值.

    Args:
        config: 插件配置字典
        *keys: 嵌套的配置键路径
        default: 默认值

    Returns:
        配置值
    """
    value = config
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
        else:
            return default
        if value is None:
            return default
    return value


def migrate_llm_tool_response_config(config: AstrBotConfig | dict) -> None:
    """将 llm_tool_silent_mode 迁移为 llm_tool_response_mode."""
    ai_behavior = config.get("ai_behavior")
    if not isinstance(ai_behavior, dict):
        return

    legacy_silent = ai_behavior.get("llm_tool_silent_mode")
    if not isinstance(legacy_silent, bool):
        return

    if legacy_silent:
        response_mode = LLM_RESPONSE_LLM_ONLY
    else:
        # v1.2.0 的非静默模式会在插件发送结果后继续让模型回复。
        response_mode = LLM_RESPONSE_RESULTS_WITH_SUMMARY

    ai_behavior["llm_tool_response_mode"] = response_mode
    ai_behavior["llm_tool_silent_mode"] = LEGACY_CONFIG_MIGRATED
    save_config = getattr(config, "save_config", None)
    if callable(save_config):
        save_config()
    logger.info("[ImgExploration] 已迁移 LLM 搜图响应模式配置")


def migrate_engine_config(config: AstrBotConfig | dict) -> None:
    """将按类型分组的旧引擎配置迁移到按引擎分组的新配置.

    仅在配置版本低于 2 时执行一次。旧分组的值保持不变，以便回退到旧版插件。

    Args:
        config: 插件配置；迁移结果直接写入该对象
    """
    version = config.get("config_version")
    if (
        isinstance(version, int)
        and not isinstance(version, bool)
        and version >= ENGINE_CONFIG_VERSION
    ):
        return

    for (old_group, old_key), (
        new_group,
        new_key,
    ) in LEGACY_ENGINE_CONFIG_KEYS.items():
        old_config = config.get(old_group)
        if not isinstance(old_config, dict) or old_config.get(old_key) is None:
            continue
        new_config = config.get(new_group)
        if not isinstance(new_config, dict):
            new_config = {}
            config[new_group] = new_config
        new_config[new_key] = old_config[old_key]

    config["config_version"] = ENGINE_CONFIG_VERSION
    save_config = getattr(config, "save_config", None)
    if callable(save_config):
        save_config()
    logger.info("[ImgExploration] 已将搜图引擎配置迁移为按引擎分组")


def normalize_image_wait_timeout(value: Any) -> int:
    """将图片等待超时配置限制在支持范围内"""
    if isinstance(value, bool):
        return DEFAULT_IMAGE_WAIT_TIMEOUT_SECONDS
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        return DEFAULT_IMAGE_WAIT_TIMEOUT_SECONDS
    return max(
        MIN_IMAGE_WAIT_TIMEOUT_SECONDS,
        min(MAX_IMAGE_WAIT_TIMEOUT_SECONDS, timeout),
    )


def normalize_search_cooldown(value: Any) -> int:
    """将搜图冷却配置归一化为非负整数，无效值视为不限制"""
    if isinstance(value, bool):
        return 0
    try:
        cooldown = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, cooldown)


def normalize_result_limit(value: Any, default: int) -> int:
    """将搜图结果上限归一化为正整数"""
    if isinstance(value, bool):
        return default
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return default
    return limit if limit > 0 else default


def apply_runtime_settings(config: dict) -> None:
    """应用网络和图片上下文配置.

    Args:
        config: 插件配置字典
    """
    # 设置网络配置
    network_config = get_nested_config(config, "network", default={})
    proxy_url = network_config.get("proxy_url", "")
    set_proxy_url(proxy_url)
    allow_image_upload = network_config.get(
        "allow_image_upload", DEFAULT_ALLOW_IMAGE_UPLOAD
    )
    set_allow_image_upload(allow_image_upload)
    allow_local_file_access = network_config.get(
        "allow_local_file_access", DEFAULT_ALLOW_LOCAL_FILE_ACCESS
    )
    set_allow_local_file_access(allow_local_file_access)

    # 初始化图片上下文管理器
    ai_behavior = get_nested_config(config, "ai_behavior", default={})
    isolation_mode = ai_behavior.get(
        "image_context_isolation", DEFAULT_IMAGE_CONTEXT_ISOLATION
    )
    max_images = ai_behavior.get(
        "max_images_per_session", DEFAULT_MAX_IMAGES_PER_SESSION
    )
    image_ttl_seconds = ai_behavior.get(
        "image_context_ttl_seconds", DEFAULT_IMAGE_CONTEXT_TTL_SECONDS
    )
    max_sessions = ai_behavior.get(
        "max_image_context_sessions", DEFAULT_MAX_IMAGE_CONTEXT_SESSIONS
    )
    init_image_context_manager(
        isolation_mode=isolation_mode,
        max_images=max_images,
        ttl_seconds=image_ttl_seconds,
        max_sessions=max_sessions,
    )


def build_strategies(config: dict) -> list[ImageSearchStrategy]:
    """按配置创建已启用且凭据完整的搜图策略.

    Args:
        config: 插件配置字典

    Returns:
        搜图策略列表
    """
    strategies: list[ImageSearchStrategy] = []

    # SauceNAO
    enable_saucenao = get_nested_config(config, "saucenao", "enable", default=True)
    saucenao_threshold = get_nested_config(
        config,
        "saucenao",
        "similarity_threshold",
        default=DEFAULT_SAUCENAO_SIMILARITY_THRESHOLD,
    )
    # 凭据去除首尾空白，避免复制粘贴带入的空格或换行导致请求失败
    sauce_nao_key = str(
        get_nested_config(config, "saucenao", "api_key", default="")
    ).strip()
    if enable_saucenao and sauce_nao_key:
        strategies.append(
            SauceNaoStrategy(
                api_key=sauce_nao_key,
                similarity_threshold=saucenao_threshold,
                max_results=normalize_result_limit(
                    get_nested_config(config, "saucenao", "max_results"),
                    DEFAULT_SAUCENAO_MAX_RESULTS,
                ),
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
    enable_google_lens = get_nested_config(
        config, "google_lens", "enable", default=True
    )
    serpapi_keys = get_nested_config(config, "google_lens", "serpapi_keys", default=[])
    if not isinstance(serpapi_keys, list):
        serpapi_keys = []
    # 忽略空 Key，避免只有空条目时加载一个必然失败的 Google Lens 策略
    serpapi_keys = [
        key.strip() for key in serpapi_keys if isinstance(key, str) and key.strip()
    ]
    if enable_google_lens and serpapi_keys:
        strategies.append(
            GoogleLensStrategy(
                api_keys=serpapi_keys,
                max_results=normalize_result_limit(
                    get_nested_config(config, "google_lens", "max_results"),
                    DEFAULT_GOOGLE_LENS_MAX_RESULTS,
                ),
                search_type=get_nested_config(
                    config,
                    "google_lens",
                    "search_type",
                    default=DEFAULT_GOOGLE_LENS_SEARCH_TYPE,
                ),
                language=get_nested_config(
                    config,
                    "google_lens",
                    "language",
                    default=DEFAULT_GOOGLE_LENS_LANGUAGE,
                ),
                country=get_nested_config(
                    config,
                    "google_lens",
                    "country",
                    default=DEFAULT_GOOGLE_LENS_COUNTRY,
                ),
                safe_search=get_nested_config(
                    config,
                    "google_lens",
                    "safe_search",
                    default=DEFAULT_GOOGLE_LENS_SAFE_SEARCH,
                ),
                auto_crop=get_nested_config(
                    config,
                    "google_lens",
                    "auto_crop",
                    default=DEFAULT_GOOGLE_LENS_AUTO_CROP,
                ),
                no_cache=get_nested_config(
                    config,
                    "google_lens",
                    "no_cache",
                    default=DEFAULT_GOOGLE_LENS_NO_CACHE,
                ),
            )
        )
        logger.info("[ImgExploration] 已加载 Google Lens 策略")
    elif enable_google_lens:
        logger.warning("[ImgExploration] SerpAPI Keys 未配置，跳过 Google Lens 策略")
    else:
        logger.info("[ImgExploration] Google Lens 策略已禁用")

    # Ascii2d
    enable_ascii2d = get_nested_config(config, "ascii2d", "enable", default=True)
    ascii2d_session_id = str(
        get_nested_config(config, "ascii2d", "session_id", default="")
    ).strip()
    ascii2d_cf_clearance = str(
        get_nested_config(config, "ascii2d", "cf_clearance", default="")
    ).strip()
    if enable_ascii2d and ascii2d_session_id:
        strategies.append(
            Ascii2dStrategy(
                session_id=ascii2d_session_id,
                cf_clearance=ascii2d_cf_clearance,
                bovw_max_results=normalize_result_limit(
                    get_nested_config(config, "ascii2d", "bovw_max_results"),
                    DEFAULT_ASCII2D_BOVW_MAX_RESULTS,
                ),
                color_max_results=normalize_result_limit(
                    get_nested_config(config, "ascii2d", "color_max_results"),
                    DEFAULT_ASCII2D_COLOR_MAX_RESULTS,
                ),
            )
        )
        logger.info("[ImgExploration] 已加载 Ascii2d 策略")
    elif enable_ascii2d:
        logger.warning("[ImgExploration] Ascii2d session_id 未配置，跳过该策略")
    else:
        logger.info("[ImgExploration] Ascii2d 策略已禁用")

    if (
        get_nested_config(
            config, "trace_moe", "enable", default=DEFAULT_ENABLE_TRACE_MOE
        )
        is True
    ):
        strategies.append(
            TraceMoeStrategy(
                api_key=str(
                    get_nested_config(config, "trace_moe", "api_key", default="")
                ).strip(),
                similarity_threshold=get_nested_config(
                    config,
                    "trace_moe",
                    "similarity_threshold",
                    default=DEFAULT_TRACE_MOE_SIMILARITY_THRESHOLD,
                ),
                cut_borders=get_nested_config(
                    config,
                    "trace_moe",
                    "cut_borders",
                    default=DEFAULT_TRACE_MOE_CUT_BORDERS,
                )
                is True,
                max_results=normalize_result_limit(
                    get_nested_config(config, "trace_moe", "max_results"),
                    DEFAULT_TRACE_MOE_MAX_RESULTS,
                ),
            )
        )
        logger.info("[ImgExploration] 已加载 trace.moe 策略")

    logger.info(f"[ImgExploration] 共加载 {len(strategies)} 个搜图策略")

    if len(strategies) == 0:
        logger.error("[ImgExploration] 没有可用的搜图策略！请检查配置。")

    return strategies
