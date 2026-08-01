"""图片搜索策略基类.

定义搜图策略的接口规范。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .models import SearchResultItem


class ImageSearchStrategy(ABC):
    """搜图策略抽象基类.

    所有搜图引擎需要实现此接口。
    """

    @abstractmethod
    def get_service_name(self) -> str:
        """获取策略名称 (用于日志或显示).

        Returns:
            策略名称字符串
        """
        pass

    @abstractmethod
    async def search(self, image_url: str) -> list[SearchResultItem]:
        """执行图片搜索.

        Args:
            image_url: 图片的 URL 地址

        Returns:
            搜索结果列表
        """
        pass

    async def close(self) -> None:
        """关闭策略并清理资源.

        子类如有需要（如维护持久连接），应重写此方法。
        默认实现不做任何操作。
        """
        pass
