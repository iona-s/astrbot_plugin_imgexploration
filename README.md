# 🔍 图片搜索插件（astrbot_plugin_imgexploration）

<div align="center">

**一个支持多引擎、LLM 工具调用的图片溯源搜索插件**

[![License: AGPL](https://img.shields.io/badge/License-AGPL-blue.svg)](https://opensource.org/licenses/agpl-3.0)
[![CI](https://github.com/iona-s/astrbot_plugin_imgexploration/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/iona-s/astrbot_plugin_imgexploration/actions/workflows/ci.yml)
![Python Version](https://img.shields.io/badge/Python-%3E%3D3.12%2C%3C4-blue)
![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.25%2C%3C5-green)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-lightgrey)

</div>


本插件完全开源免费，欢迎 Issue 和 PR。

本项目由[FlanChanXwO](https://github.com/FlanChanXwO)创建，感谢他对插件的贡献
现由[iona-s](https://github.com/iona-s)继续维护

---

## 📸 预览

<div align="center">
  <table>
    <tr>
      <td align="center">
        <img src="https://raw.githubusercontent.com/iona-s/astrbot_plugin_imgexploration/master/assets/google_search.png" width="400" alt="谷歌搜图"/>
        <br/>
        <sub>谷歌搜图</sub>
      </td>
      <td align="center">
        <img src="https://raw.githubusercontent.com/iona-s/astrbot_plugin_imgexploration/master/assets/google_search_llm.png" width="400" alt="谷歌LLM搜图"/>
        <br/>
        <sub>谷歌LLM搜图</sub>
      </td>
      <td align="center">
        <img src="https://raw.githubusercontent.com/iona-s/astrbot_plugin_imgexploration/master/assets/sauce_search.png" width="400" alt="sauce搜图"/>
        <br/>
        <sub>sauce搜图</sub>
      </td>
      <td align="center">
        <img src="https://raw.githubusercontent.com/iona-s/astrbot_plugin_imgexploration/master/assets/2d_search.jpg" width="400" alt="2d搜图"/>
        <br/>
        <sub>2d搜图</sub>
      </td>
    </tr>
  </table>
</div>

---

## 📦 安装

### 环境要求

- Python `>=3.12,<4`
- AstrBot `>=4.25,<5`

### 方式一：通过 AstrBot 插件市场安装（推荐）

在 AstrBot 管理面板中搜索 `astrbot_plugin_imgexploration` 并安装。

### 方式二：手动安装

1. 克隆本仓库到 AstrBot 的插件目录：
   ```bash
   cd AstrBot/data/plugins
   git clone https://github.com/iona-s/astrbot_plugin_imgexploration.git
   ```
2. 安装依赖：
   ```bash
   pip install -r requirements.txt
   ```
3. 重启 AstrBot 或重载插件

---

## 🛠️ 配置项

### API 密钥配置

| 配置项 | 类型 | 说明 | 必填 |
|--------|------|------|------|
| `saucenao_api_key` | 字符串 | SauceNAO API Key | 是（使用该引擎） |
| `serpapi_keys` | 列表 | SerpAPI Keys（Google Lens） | 是（使用该引擎） |
| `ascii2d_session_id` | 字符串 | Ascii2d Session ID | 是（使用该引擎） |
| `ascii2d_cf_clearance` | 字符串 | Ascii2d cf_clearance（绕过 CF） | 否 |

### 搜图策略配置

| 配置项 | 类型 | 说明 | 默认值 |
|--------|------|------|--------|
| `enable_saucenao` | 布尔值 | 启用 SauceNAO | `true` |
| `saucenao_similarity_threshold` | 整数 | SauceNAO 相似度阈值 | `40` |
| `enable_google_lens` | 布尔值 | 启用 Google Lens | `true` |
| `enable_ascii2d` | 布尔值 | 启用 Ascii2d | `true` |

### AI 行为配置

| 配置项 | 类型 | 说明 | 默认值 |
|--------|------|------|--------|
| `image_context_isolation` | 字符串 | 图片上下文隔离模式（`session`/`global`） | `session` |
| `max_images_per_session` | 整数 | 每会话最大图片数 | `20` |
| `image_context_ttl_seconds` | 整数 | 图片上下文保留时长（秒），0 表示不过期 | `0` |
| `max_image_context_sessions` | 整数 | 最大图片上下文会话数（LRU 回收） | `200` |
| `include_image_url_in_context` | 布尔值 | 在 AI 图片上下文中包含原始 URL | `true` |
| `llm_tool_silent_mode` | 布尔值 | LLM 工具静默模式，开启后不自动发送消息 | `false` |

### 命令配置

| 配置项 | 类型 | 说明 | 默认值 |
|--------|------|------|--------|
| `image_wait_timeout_seconds` | 整数 | 单独发送搜图命令后等待图片的秒数，范围 30–120 | `60` |

### 显示配置

| 配置项 | 类型 | 说明 | 默认值 |
|--------|------|------|--------|
| `max_results` | 整数 | 每个引擎返回的最大结果数量 | `5` |

### 网络配置

| 配置项 | 类型 | 说明 | 默认值 |
|--------|------|------|--------|
| `proxy_url` | 字符串 | 代理服务器 URL | 空 |
| `user_agent` | 字符串 | User-Agent | Chrome 120 |
| `allow_image_upload` | 布尔值 | 允许上传图片到第三方图床 | `true` |
| `allow_local_file_access` | 布尔值 | 允许读取本地文件 | `false` |

---

## ⚠️ 隐私说明

### 本地文件访问

出于安全考虑，插件默认 **禁止** 读取本地文件（`file://` 路径或磁盘路径如 `C:\path`）。

**风险说明：**
- 允许读取本地文件可能导致服务器上任意可访问文件被上传到第三方图床
- 恶意用户可能利用此功能探测服务器文件结构

**如何开启：**
如确需使用本地文件搜图，在配置中设置 `allow_local_file_access = true`。建议仅在受信任的环境中使用。

### 图床上传

当搜图引擎需要 HTTP URL 时（如 SauceNAO、Google Lens），如果用户发送的是本地文件、`file://` 路径或 `base64` 图片，插件会将图片上传到 **Catbox (catbox.moe)** 第三方图床以获取公开可访问的 URL。

**这意味着：**
- 图片内容会暴露给 Catbox 第三方服务
- 上传后的图片 URL 可能被他人访问
- Catbox 服务条款请参考：https://catbox.moe

**如何关闭：**
在配置中设置 `allow_image_upload = false`，此时插件仅支持 HTTP URL 图片搜图，本地图片/base64 图片将被拒绝。

**建议：**
- 对隐私敏感的场景，关闭此功能
- 仅使用 HTTP URL 图片进行搜图
- 或自行搭建私有图床并修改代码

---

## 📝 使用方法

### 命令方式

可以在同一条消息中附带图片、回复一张图片，或先发送命令再发送图片：

```
搜图
搜图 saucenao
搜图 google
搜图 ascii2d
搜图 saucenao,google
搜图 sauce,2d
```

如果命令消息包含多张图片，固定使用第一张。命令消息同时包含附图和回复时，
优先使用当前消息附带的图片。

回复一条不含图片的消息发送命令时，插件会提示
`回复消息中未找到图片`，且不会进入图片等待。

单独发送有效命令后，插件会等待同一发送者在同一会话中的下一条含图消息。
等待期间的纯文本、其他成员和其他会话不会消费该状态；再次发送无图命令会
提示 `当前已进入搜索模式，请直接发送图片`，且不会改变原截止时间或搜索策略。
即使没有收到后续消息，插件也会在截止时间到达后自动发送
`搜图等待已超时`。

**命令别名：**
- `sauce` = `saucenao`
- `2d` = `ascii2d`

### LLM 工具调用

确保使用支持 Function Calling 的模型（如 GPT-4、Claude、DeepSeek 等），然后：

1. 发送一张图片
2. 说"找一下这张图的来源"或"搜图"
3. AI 会自动调用搜图工具

**注意：** 需要使用支持 `tool_use` 的模型。如果日志显示 `does not support tool_use`，请切换模型。

---

## 🔧 获取 API 密钥

### SauceNAO

1. 访问 https://saucenao.com/user.php?page=search-api
2. 注册/登录账号
3. 获取 API Key

### SerpAPI（Google Lens）

1. 访问 https://serpapi.com
2. 注册账号（免费版每月 250 次）
3. 获取 API Key

### Ascii2d

1. 用 **Chrome 浏览器**访问 https://ascii2d.net
2. 上传一个图片然后搜索图片随后进行跳转
3. 通过 Cloudflare 验证
4. 打开开发者工具 (F12) → Application → Cookies
5. 复制 `_session_id` Cookie 的值，填入配置项 `ascii2d_session_id`
6. 复制 `cf_clearance` Cookie 的值，填入配置项 `ascii2d_cf_clearance`

---

## 📖 引擎说明

| 引擎 | 特点 | 适用场景 |
|------|------|----------|
| **SauceNAO** | 动漫图片专精，支持 Pixiv、Danbooru 等数据库 | 动漫插画、二次元图片 |
| **Google Lens** | 通用图片搜索，覆盖面广 | 通用图片、商品、风景 |
| **Ascii2d** | 日本搜图网站，支持色合/特征搜索 | 动漫图片、Pixiv 插画 |

---

## ⚠️ 常见问题

### 1. Ascii2d 返回 403 错误

解决：重新获取cookie，或尝试使用代理

> 目前2d的反爬还是挺强的，后续会优化这个搜图方式

### 2. 图片没有被识别

解决：
- 确保图片发送后有时间被捕获
- 检查日志是否显示"捕获图片到上下文"

---

## 🤝 开发与贡献

欢迎提交 Issue 和 Pull Request。参与开发前请阅读
[贡献指南](CONTRIBUTING.md)。

---

## 📄 开源协议

本项目基于 [AGPL-3.0](LICENSE) 协议开源。

---

## 🙏 致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) - 强大的 AI 助手框架
- [SauceNAO](https://saucenao.com) - 动漫图片搜索引擎
- [SerpAPI](https://serpapi.com) - Google Lens API 服务
- [Ascii2d](https://ascii2d.net) - 日本图片搜索网站
