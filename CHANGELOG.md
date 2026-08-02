# Changelog

## [1.1.1] - 2026-08-02

### Added

- 新增 `enable_llm_tools` 配置，可在不影响命令搜图的情况下关闭 LLM 搜图工具

### Changed

- 将未生效的全局 `max_results` 配置替换为 SauceNAO、Google Lens、Ascii2d BOVW 和 Ascii2d 色合的独立结果上限
- 收紧 LLM 搜图工具的调用说明，仅在用户明确提出搜图、查找来源或反向图片搜索时建议模型调用
- 将运行环境要求调整为 Python `>=3.12,<4`、AstrBot `>=4.25,<5` 和 `curl_cffi>=0.9.0`

### Fixed

- 修复等待流程消费图片后仍继续向下游传播，导致图片同时进入普通聊天处理的问题
- 调整图片捕获与搜图日志的等级和内容

## [1.1.0] - 2026-07-29

### Added

- 命令搜图在开始处理图片前发送固定确认消息
- 支持在 `搜图` 命令消息中直接附带图片
- 支持单独发送 `搜图` 后，在同一会话中等待同一发送者继续发送图片
- 新增 `image_wait_timeout_seconds` 配置，用于设置等待图片的超时时间

### Fixed

- 恢复 AstrBot 媒体预处理后丢失的 OneBot 图片 URL，并用于图片上下文和命令搜图
- 统一命令附图、回复图片和本地来源的解析顺序及 HTTP URL 优先规则
- 兼容 AstrBot 的 LLM 工具生命周期管理，避免插件卸载时调用已移除的框架接口
- 修复 SerpAPI 多 Key 成功请求不轮换，以及遇到 HTTP 429 后不切换可用 Key 的问题

## [1.0.3] - 2026-04-03

### Changed

- 增加图片转发降级，避免因为其中一个策略导致整个搜索结果发送失败

## [1.0.0] - 2026-04-03

### Added

- **多引擎支持**
  - SauceNAO 搜索引擎（动漫图片专精）
  - Google Lens 搜索引擎（通过 SerpAPI）
  - Ascii2d 搜索引擎（日本搜图网站）

- **命令搜图**
  - 支持 `搜图` 命令触发搜索
  - 支持指定引擎：`搜图 saucenao`、`搜图 google`、`搜图 ascii2d`
  - 支持引擎别名：`sauce`、`2d`

- **LLM 工具调用**
  - `get_session_images` - 查询会话中的图片列表
  - `search_image` - 执行图片搜索
  - 搜索结果自动发送给用户，AI 只需总结

- **图片上下文管理器**
  - 自动捕获会话中的图片
  - 支持会话级隔离（每个群/私聊独立）
  - 支持全局隔离（所有会话共享）
  - 可配置每会话最大图片数

- **网络配置**
  - 支持自定义 User-Agent
  - 支持代理设置
  - 使用 curl_cffi 绕过 Cloudflare（模拟 Chrome 120 TLS 指纹）

- **多平台适配**
  - aiocqhttp 平台：合并转发消息
  - 其他平台：消息链发送

### Changed

- LLM 工具参数从 `image_url` 改为 `image_index`，更易使用
- AI 必须展示搜索结果的 URL 和来源名称
- 使用 `curl_cffi` 替代 `aiohttp` 请求 Ascii2d（绕过 Cloudflare）
- 图片上下文使用 `OrderedDict` 存储，支持 LRU 淘汰
- 全局共享 `aiohttp.ClientSession` 提升性能
