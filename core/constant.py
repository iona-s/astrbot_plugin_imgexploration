"""图片搜索插件常量定义."""

# ==============================================================================
# 网络请求相关
# ==============================================================================

# HTTP 请求超时时间 (秒)
HTTP_TIMEOUT_SECONDS = 30

# 下载图片超时时间 (秒)
IMAGE_DOWNLOAD_TIMEOUT = 20

# 单次下载内容上限 (字节)，防止异常大的响应耗尽内存
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024

# Catbox 图床单文件上传上限 (字节)；本地图片只用于上传，读取上限与其一致
CATBOX_MAX_UPLOAD_BYTES = 200 * 1024 * 1024

# 默认 User-Agent (与 curl_cffi impersonate chrome120 保持一致)
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# 模拟浏览器的完整请求头
BROWSER_HEADERS = {"User-Agent": DEFAULT_USER_AGENT}

# 是否允许上传图片到第三方图床、读取本地文件的默认值
DEFAULT_ALLOW_IMAGE_UPLOAD = True
DEFAULT_ALLOW_LOCAL_FILE_ACCESS = False

# ==============================================================================
# 图片上下文
# ==============================================================================

DEFAULT_IMAGE_CONTEXT_ISOLATION = "session"
DEFAULT_MAX_IMAGES_PER_SESSION = 20
DEFAULT_IMAGE_CONTEXT_TTL_SECONDS = 0
DEFAULT_MAX_IMAGE_CONTEXT_SESSIONS = 200
DEFAULT_INCLUDE_IMAGE_URL_IN_CONTEXT = True

# LLM 搜图结果展示模式默认值，与旧版关闭静默模式时的行为一致
DEFAULT_LLM_TOOL_RESPONSE_MODE = "results_with_summary"

# ==============================================================================
# SauceNAO 策略
# ==============================================================================

# SauceNAO API 地址
SAUCENAO_BASE_URL = "https://saucenao.com/search.php"

# SauceNAO 默认结果上限
DEFAULT_SAUCENAO_MAX_RESULTS = 3

# SauceNAO 默认相似度阈值 (%)
DEFAULT_SAUCENAO_SIMILARITY_THRESHOLD = 40

# ==============================================================================
# Google Lens 策略
# ==============================================================================

# SerpAPI 基础 URL
SERPAPI_BASE_URL = "https://serpapi.com"

# Google Lens 默认结果上限
DEFAULT_GOOGLE_LENS_MAX_RESULTS = 5

# Google Lens 搜索选项默认值；搜索类型与旧版不发送 type 时 SerpAPI 的默认值一致
DEFAULT_GOOGLE_LENS_SEARCH_TYPE = "all"
DEFAULT_GOOGLE_LENS_LANGUAGE = "zh-cn"
DEFAULT_GOOGLE_LENS_COUNTRY = ""
DEFAULT_GOOGLE_LENS_SAFE_SEARCH = False
DEFAULT_GOOGLE_LENS_AUTO_CROP = False
DEFAULT_GOOGLE_LENS_NO_CACHE = False

# ==============================================================================
# Ascii2d 策略
# ==============================================================================

# Ascii2d 基础 URL
ASCII2D_BASE_URL = "https://ascii2d.net"

# Ascii2d 搜索 URL
ASCII2D_SEARCH_URI_URL = f"{ASCII2D_BASE_URL}/search/uri"

# Ascii2d 默认结果上限
DEFAULT_ASCII2D_BOVW_MAX_RESULTS = 3
DEFAULT_ASCII2D_COLOR_MAX_RESULTS = 2

# ==============================================================================
# 策略名称映射
# ==============================================================================

# 策略名称映射 (小写 -> 策略类名关键字)
STRATEGY_ALIAS_MAP = {
    "saucenao": "SauceNAO",
    "sauce": "SauceNAO",
    "google": "Google Lens",
    "googlelens": "Google Lens",
    # 命令参数按空白分隔时，“Google Lens” 会被拆成 google 和 lens
    "lens": "Google Lens",
    "ascii2d": "Ascii2d",
    "ascii": "Ascii2d",
    "2d": "Ascii2d",
}
