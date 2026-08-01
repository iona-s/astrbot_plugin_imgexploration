"""图片搜索插件常量定义."""

# ==============================================================================
# 网络请求相关
# ==============================================================================

# HTTP 请求超时时间 (秒)
HTTP_TIMEOUT_SECONDS = 30

# 下载图片超时时间 (秒)
IMAGE_DOWNLOAD_TIMEOUT = 20

# 默认 User-Agent (与 curl_cffi impersonate chrome120 保持一致)
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# 模拟浏览器的完整请求头
BROWSER_HEADERS = {"User-Agent": DEFAULT_USER_AGENT}

# ==============================================================================
# SauceNAO 策略
# ==============================================================================

# SauceNAO API 地址
SAUCENAO_BASE_URL = "https://saucenao.com/search.php"

# ==============================================================================
# Google Lens 策略
# ==============================================================================

# SerpAPI 基础 URL
SERPAPI_BASE_URL = "https://serpapi.com"

# ==============================================================================
# Ascii2d 策略
# ==============================================================================

# Ascii2d 基础 URL
ASCII2D_BASE_URL = "https://ascii2d.net"

# Ascii2d 搜索 URL
ASCII2D_SEARCH_URI_URL = f"{ASCII2D_BASE_URL}/search/uri"

# ==============================================================================
# 策略名称映射
# ==============================================================================

# 策略名称映射 (小写 -> 策略类名关键字)
STRATEGY_ALIAS_MAP = {
    "saucenao": "SauceNAO",
    "sauce": "SauceNAO",
    "google": "Google Lens",
    "googlelens": "Google Lens",
    "ascii2d": "Ascii2d",
    "ascii": "Ascii2d",
    "2d": "Ascii2d",
}
