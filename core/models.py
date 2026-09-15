"""图片搜索插件数据模型.

定义搜索结果的数据结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field


class ProviderSearchError(Exception):
    """提供商预期搜索失败异常.

    当提供商因凭据、HTTP、API 或外部服务问题无法完成搜索时抛出。
    此异常表示搜索失败，而非零匹配结果。
    """

    def __init__(self, message: str) -> None:
        """初始化提供商搜索错误.

        Args:
            message: 错误描述信息，仅用于内部日志，不应暴露给用户
        """
        super().__init__(message)
        self.message = message


@dataclass
class SearchResultItem:
    """单个搜索结果项."""

    title: str
    url: str
    thumbnail: str = ""
    thumbnail_bytes: bytes | None = None
    source: str = ""
    similarity: str | None = None
    description: str | None = None
    domain: str | None = None

    def with_thumbnail_bytes(self, bytes_data: bytes) -> SearchResultItem:
        """返回带有缩略图字节的新实例."""
        return SearchResultItem(
            title=self.title,
            url=self.url,
            thumbnail=self.thumbnail,
            thumbnail_bytes=bytes_data,
            source=self.source,
            similarity=self.similarity,
            description=self.description,
            domain=self.domain,
        )


@dataclass
class ProviderSearchOutcome:
    """带有可选用户提示的提供商搜索结果."""

    items: list[SearchResultItem] = field(default_factory=list)
    user_notices: list[str] = field(default_factory=list)


@dataclass
class ExplorationResult:
    """搜索结果集合."""

    items: list[SearchResultItem] = field(default_factory=list)
    attempted_providers: list[str] = field(default_factory=list)
    failed_providers: list[str] = field(default_factory=list)
    user_notices: list[str] = field(default_factory=list)

    @property
    def all_failed(self) -> bool:
        """是否所有尝试的提供商都失败了."""
        return bool(self.attempted_providers) and len(self.failed_providers) == len(
            self.attempted_providers
        )
