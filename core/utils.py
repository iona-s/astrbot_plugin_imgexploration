"""图片搜索插件工具函数.

包含 HTTP 请求、图片下载、图床上传等通用功能。
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
from typing import Any

import aiohttp
from astrbot.api import logger

from .constant import DEFAULT_USER_AGENT, HTTP_TIMEOUT_SECONDS, IMAGE_DOWNLOAD_TIMEOUT

# Catbox 图床 URL
CATBOX_UPLOAD_URL = "https://catbox.moe/user/api.php"
# 全局共享的 aiohttp ClientSession
_aiohttp_session: aiohttp.ClientSession | None = None
_aiohttp_session_lock = asyncio.Lock()
# 全局代理设置
_proxy_url: str | None = None
# 全局 User-Agent 设置
_user_agent: str | None = None
# 是否允许上传图片到第三方图床
_allow_image_upload: bool = True
# 是否允许读取本地文件
_allow_local_file_access: bool = False

# 敏感的 URL 查询参数名（日志中需要隐藏）
SENSITIVE_QUERY_PARAMS = frozenset(
    {
        "api_key",
        "key",
        "token",
        "secret",
        "password",
        "pass",
        "session_id",
        "sessionid",
        "auth",
        "access_token",
        "apikey",
    }
)


def _sanitize_url_for_logging(url: str) -> str:
    """清理 URL 中的敏感信息，用于日志输出.

    隐藏敏感查询参数的值，只显示参数名。

    Args:
        url: 原始 URL

    Returns:
        清理后的 URL，适合日志输出
    """
    if not url:
        return url

    try:
        # 分离 URL 的各个部分
        if "?" not in url:
            return url

        base, query = url.split("?", 1)
        if "#" in query:
            query, fragment = query.split("#", 1)
            fragment = "#" + fragment
        else:
            fragment = ""

        # 处理查询参数
        params = query.split("&")
        sanitized_params = []
        for param in params:
            if "=" in param:
                key, _ = param.split("=", 1)
                if key.lower() in SENSITIVE_QUERY_PARAMS:
                    # 隐藏敏感参数值
                    sanitized_params.append(f"{key}=***REDACTED***")
                else:
                    sanitized_params.append(param)
            else:
                sanitized_params.append(param)

        return f"{base}?{'&'.join(sanitized_params)}{fragment}"
    except Exception:
        # 如果解析失败，返回一个安全的占位符
        return "<URL removed for security>"


def set_proxy_url(proxy_url: str | None) -> None:
    """设置全局代理 URL.

    Args:
        proxy_url: 代理 URL，如 http://127.0.0.1:7890。None 或空字符串表示不使用代理。
    """
    global _proxy_url
    if (
        proxy_url
        and proxy_url.strip()
        and proxy_url.startswith(("http://", "https://"))
    ):
        _proxy_url = proxy_url.strip()
        logger.info(f"[ImgExploration] 已设置代理: {_proxy_url}")
    else:
        _proxy_url = None
        logger.debug("[ImgExploration] 未设置有效代理，将直接连接")


def get_proxy_url() -> str | None:
    """获取当前代理 URL.

    Returns:
        代理 URL，未设置则返回 None
    """
    return _proxy_url


def set_user_agent(user_agent: str | None) -> None:
    """设置全局 User-Agent.

    Args:
        user_agent: User-Agent 字符串。None 或空字符串表示使用默认值。
    """
    global _user_agent
    if user_agent and user_agent.strip():
        _user_agent = user_agent.strip()
        logger.info(f"[ImgExploration] 已设置 User-Agent: {_user_agent[:50]}...")
    else:
        _user_agent = None
        logger.debug("[ImgExploration] 未设置自定义 User-Agent，将使用默认值")


def get_user_agent() -> str:
    """获取当前 User-Agent.

    Returns:
        User-Agent 字符串，未设置则返回默认值
    """
    if _user_agent:
        return _user_agent
    return DEFAULT_USER_AGENT


def set_allow_image_upload(allow: bool) -> None:
    """设置是否允许上传图片到第三方图床.

    Args:
        allow: True 允许上传，False 禁止上传
    """
    global _allow_image_upload
    _allow_image_upload = allow
    status = "允许" if allow else "禁止"
    logger.info(f"[ImgExploration] 已设置图床上传策略: {status}")


def is_image_upload_allowed() -> bool:
    """检查是否允许上传图片到第三方图床.

    Returns:
        True 如果允许上传
    """
    return _allow_image_upload


def set_allow_local_file_access(allow: bool) -> None:
    """设置是否允许读取本地文件.

    Args:
        allow: True 允许读取，False 禁止读取
    """
    global _allow_local_file_access
    _allow_local_file_access = allow
    status = "允许" if allow else "禁止"
    logger.info(f"[ImgExploration] 已设置本地文件访问策略: {status}")


def is_local_file_access_allowed() -> bool:
    """检查是否允许读取本地文件.

    Returns:
        True 如果允许读取
    """
    return _allow_local_file_access


async def get_aiohttp_session() -> aiohttp.ClientSession:
    """获取全局共享的 aiohttp ClientSession.

    注意：
        代理配置通过每次请求时传入 `proxy=get_proxy_url()` 来控制，
        因此会话本身不配置代理连接器。

    Returns:
        全局共享的 ClientSession 实例
    """
    global _aiohttp_session
    # 双重检查锁，避免在高并发时重复创建 ClientSession
    if _aiohttp_session is not None and not _aiohttp_session.closed:
        return _aiohttp_session

    async with _aiohttp_session_lock:
        if _aiohttp_session is None or _aiohttp_session.closed:
            # 不使用自定义 connector，代理由各请求通过 `proxy` 参数控制
            _aiohttp_session = aiohttp.ClientSession()
        return _aiohttp_session


async def close_aiohttp_session() -> None:
    """关闭全局共享的 aiohttp ClientSession.

    在插件卸载时调用，避免资源泄漏和事件循环清理警告。
    """
    global _aiohttp_session
    if _aiohttp_session is not None and not _aiohttp_session.closed:
        await _aiohttp_session.close()
        logger.debug("[ImgExploration] aiohttp ClientSession 已关闭")
    _aiohttp_session = None


async def download_bytes(
    url: str,
    timeout_seconds: int = IMAGE_DOWNLOAD_TIMEOUT,
    headers: dict[str, str] | None = None,
) -> bytes | None:
    """下载指定 URL 的内容并返回字节数据.

    Args:
        url: 要下载的 URL
        timeout_seconds: 请求超时时间 (秒)
        headers: 自定义请求头

    Returns:
        下载的字节数据，失败返回 None
    """
    if not url or not url.startswith(("http://", "https://")):
        return None

    default_headers = {"User-Agent": get_user_agent()}
    if headers:
        default_headers.update(headers)

    client_timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    proxy = get_proxy_url()

    try:
        session = await get_aiohttp_session()
        async with session.get(
            url, timeout=client_timeout, headers=default_headers, proxy=proxy
        ) as resp:
            if resp.status == 200:
                return await resp.read()
    except Exception as e:
        logger.debug(
            f"[ImgExploration] 下载失败: {_sanitize_url_for_logging(url)}, 错误: {e}"
        )

    return None


async def download_bytes_batch(
    urls: list[str],
    timeout_seconds: int = IMAGE_DOWNLOAD_TIMEOUT,
    headers: dict[str, str] | None = None,
) -> list[bytes | None]:
    """批量下载多个 URL 的内容.

    Args:
        urls: URL 列表
        timeout_seconds: 每个请求的超时时间 (秒)
        headers: 自定义请求头

    Returns:
        字节数据列表，每个位置对应输入 URL 列表的位置
    """
    tasks = [download_bytes(url, timeout_seconds, headers) for url in urls]
    return await asyncio.gather(*tasks, return_exceptions=False)


def is_aiocqhttp_platform(event: Any) -> bool:
    """检测当前平台是否为 aiocqhttp (支持合并转发).

    Args:
        event: AstrBot 消息事件

    Returns:
        True 如果是 aiocqhttp 平台
    """
    platform = getattr(event, "platform", None)
    if platform:
        return "aiocqhttp" in str(platform).lower()
    return False


def get_bot_api(event: Any) -> Any | None:
    """从事件中获取底层 bot API 客户端.

    Args:
        event: AstrBot 消息事件

    Returns:
        bot API 客户端对象，失败返回 None
    """
    return getattr(event, "bot", None)


def _read_file_bytes(file_path: str) -> bytes:
    """读取本地文件字节数据（用于 to_thread 调用）.

    使用上下文管理器确保文件句柄正确关闭。

    Args:
        file_path: 文件路径

    Returns:
        文件字节数据
    """
    with open(file_path, "rb") as f:
        return f.read()


async def read_image_bytes(source: str) -> bytes | None:
    r"""读取图片源并返回字节数据

    支持多种格式：
    - HTTP/HTTPS URL: 下载图片
    - file:// 本地文件路径: 读取本地文件（需要 allow_local_file_access）
    - Windows 本地路径 (C:\path 或 D:/path): 读取本地文件（需要 allow_local_file_access）
    - POSIX 本地路径 (/home/user/xxx.png): 读取本地文件（需要 allow_local_file_access）
    - base64:// 数据: 解码 base64
    - data:image/...;base64,... : 解码 base64 data URI

    注意：
        本地文件访问受 allow_local_file_access 配置控制。
        出于安全考虑，默认禁用本地文件访问，以防止潜在的文件系统信息泄露。

    Args:
        source: 图片源字符串

    Returns:
        图片字节数据，失败返回 None
    """
    if not source:
        return None

    # HTTP/HTTPS URL - 直接下载
    if source.startswith(("http://", "https://")):
        return await download_bytes(source)

    # 本地文件访问 - 需要明确启用
    # 支持:
    #   - file:// 路径
    #   - Windows 绝对路径 (C:\path 或 C:/path)
    #   - POSIX 绝对路径 (/home/user/xxx.png)
    if (
        source.startswith("file://")
        or re.match(r"^[A-Za-z]:[/\\]", source)
        or source.startswith("/")
    ):
        if not is_local_file_access_allowed():
            logger.warning(
                "[ImgExploration] 本地文件访问已被禁用。"
                "如需读取本地文件，请在配置中开启 allow_local_file_access。"
            )
            return None

        # 解析文件路径
        if source.startswith("file://"):
            file_path = source[7:]  # 去掉 file:// 前缀
            # 处理 Windows 路径 (file:///C:/...)
            if file_path.startswith("/") and len(file_path) > 2 and file_path[2] == ":":
                file_path = file_path[1:]  # 去掉开头的 /
        else:
            file_path = source

        try:
            if os.path.exists(file_path):
                return await asyncio.to_thread(_read_file_bytes, file_path)
        except Exception as e:
            logger.debug(f"[ImgExploration] 读取本地文件失败: 错误: {e}")
        return None

    # base64:// 格式
    if source.startswith("base64://"):
        try:
            return base64.b64decode(source[9:])
        except Exception as e:
            logger.debug(f"[ImgExploration] base64 解码失败: {e}")
        return None

    # data:image/...;base64,... 格式
    if source.startswith("data:image"):
        # 提取 base64 部分
        match = re.match(r"data:image/\w+;base64,(.+)", source)
        if match:
            try:
                return base64.b64decode(match.group(1))
            except Exception as e:
                logger.debug(f"[ImgExploration] data URI 解码失败: {e}")
        return None

    return None


async def upload_image(image_bytes: bytes) -> str | None:
    """将图片上传到图床.

    使用 Catbox 图床（免费，无需 API key）。

    Args:
        image_bytes: 图片字节数据

    Returns:
        上传后的图片 URL，失败返回 None
    """
    if not image_bytes:
        return None

    # 限制文件大小 (Catbox 限制 200MB)
    if len(image_bytes) > 200 * 1024 * 1024:
        logger.warning("[ImgExploration] 图片过大，超过 200MB 限制")
        return None

    client_timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
    proxy = get_proxy_url()

    try:
        session = await get_aiohttp_session()
        # 构建 multipart form data
        data = aiohttp.FormData()
        data.add_field("reqtype", "fileupload")
        data.add_field(
            "fileToUpload",
            image_bytes,
            filename="image.jpg",
            content_type="image/jpeg",
        )

        headers = {"User-Agent": get_user_agent()}

        async with session.post(
            CATBOX_UPLOAD_URL,
            data=data,
            headers=headers,
            timeout=client_timeout,
            proxy=proxy,
        ) as resp:
            if resp.status == 200:
                url = await resp.text()
                # Catbox 直接返回图片 URL
                if url and url.startswith("https://"):
                    return url.strip()
                logger.warning(f"[ImgExploration] Catbox 返回异常: {url}")
            else:
                text = await resp.text()
                logger.warning(
                    f"[ImgExploration] Catbox 上传失败: HTTP {resp.status}, {text}"
                )
    except Exception as e:
        logger.error(f"[ImgExploration] Catbox 上传异常: {e}")

    return None


async def get_http_image_url(source: str) -> str | None:
    """将图片源转换为 HTTP URL.

    如果已经是 HTTP URL，直接返回。
    如果是本地文件或 base64，且允许图床上传，则上传后返回 URL。

    注意：
        本地图片/base64 图片会被上传到 Catbox (catbox.moe) 第三方图床。
        这意味着图片内容会暴露给第三方服务。如果隐私敏感，请关闭
        allow_image_upload 配置项，此时仅支持 HTTP URL 图片。

    Args:
        source: 图片源字符串

    Returns:
        HTTP URL，失败返回 None
    """
    if not source:
        return None

    # 已经是 HTTP URL，直接返回
    if source.startswith(("http://", "https://")):
        return source

    # 检查是否允许上传到第三方图床
    if not is_image_upload_allowed():
        logger.warning(
            "[ImgExploration] 图片上传已被禁用，仅支持 HTTP URL 图片搜图。"
            "如需搜本地图片，请在配置中开启 allow_image_upload。"
        )
        return None

    # 其他格式：读取并上传
    image_bytes = await read_image_bytes(source)
    if not image_bytes:
        return None

    return await upload_image(image_bytes)
