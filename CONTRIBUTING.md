# 贡献指南

感谢你愿意改进 `astrbot_plugin_imgexploration`。提交 Issue 或 Pull Request
前，请先搜索现有 Issues 和 Discussions，避免重复反馈或实现。

## 开始开发前

- 较大的功能、行为变更或兼容性调整应先通过 Issue 或 Discussion 讨论。
- 每个 Pull Request 应只处理一个明确问题。
- 行为或配置发生变化时，应同步更新测试和用户文档。

## 使用 Coding Agent

你可以使用 Coding Agent 辅助分析、编写代码、补充测试或整理文档，但不能
将开发与审核过程完全交由 Agent 完成。

提交者必须：

- 理解并人工审核提交中的全部代码、配置和文档；
- 检查差异中是否包含无关改动、敏感信息或不符合项目约束的内容；
- 自行执行适当的测试，并如实记录实际结果和未执行的检查；
- 对提交内容、测试结论和潜在影响负责。

使用 Coding Agent 开发时，还应阅读仓库根目录的
[AGENTS.md](AGENTS.md)。

## 开发环境

本项目支持 Python `>=3.12,<4` 和 AstrBot `>=4.25,<5`。本地开发与验证应
使用 Python 3.12，以便与 CI 和 Ruff 目标保持一致。

## 验证

根据改动范围运行相应的聚焦测试。提交 Python 改动时，通常还应执行：

```text
python -m pytest
pre-commit run --all-files
```

需要外部服务或聊天环境的现场测试无法执行时，请在 Pull Request 中明确
说明，不要将未执行的测试报告为通过。

## Commit 信息

Commit 首行允许使用中文或英文，但优先使用英文，并且必须严格采用：

```text
<type>: <summary>
```

`type` 使用小写 Conventional Commit 类型，例如 `fix`、`feat`、`docs`、
`test`、`refactor` 或 `chore`。使用英文半角冒号和一个空格，不添加 scope。

示例：

```text
fix: resolve image URLs from raw OneBot events
docs: 补充插件安装说明
```

## Pull Request

提交 Pull Request 时，请：

- 说明改动动机、实现范围和关联 Issue；
- 列出实际执行的测试及结果；
- 明确尚未执行的现场测试；
- 标注破坏性变更和新增依赖；
- 完成 Pull Request 模板中的人工审核确认。
