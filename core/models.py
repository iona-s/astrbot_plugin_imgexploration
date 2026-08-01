"""图片搜索插件数据模型.

定义搜索结果的数据结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
class ExplorationResult:
    """搜索结果集合."""

    items: list[SearchResultItem] = field(default_factory=list)
