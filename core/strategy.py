"""图片搜索策略基类.

定义搜图策略的接口规范。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .models import ProviderSearchOutcome, SearchResultItem


class ImageSearchStrategy(ABC):
    """搜图策略抽象基类.

    所有搜图引擎需要实现此接口。

    搜索约定:
    - 返回空列表 [] 表示搜索成功但没有匹配结果
    - 返回 ProviderSearchOutcome 表示搜索成功并附带用户可见提示
    - 抛出 ProviderSearchError 表示提供商预期失败（凭据、HTTP、API 错误等），
      可附带 user_notice 向用户说明原因
    - 抛出其他异常表示未预期的内部错误
    - 结果项只需填写缩略图 URL，缩略图由服务层统一下载
    - can_search_file() 返回 True 时 search() 会收到该图片的字节；否则服务层
      只传入公开的图片 URL（必要时先上传图床）
    """

    @abstractmethod
    def get_service_name(self) -> str:
        """获取策略名称 (用于日志或显示).

        Returns:
            策略名称字符串
        """
        pass

    @abstractmethod
    async def search(
        self, image: str | bytes
    ) -> list[SearchResultItem] | ProviderSearchOutcome:
        """执行图片搜索.

        Args:
            image: 图片 URL；can_search_file() 返回 True 时也可能是图片字节

        Returns:
            搜索结果列表，或带有用户提示的搜索结果
        """
        pass

    def can_search_file(self, image: bytes) -> bool:
        """判断能否直接上传该图片搜索.

        Args:
            image: 图片字节

        Returns:
            能直接上传时返回 True；默认不支持，由服务层改用图片 URL
        """
        return False

    async def close(self) -> None:
        """关闭策略并清理资源.

        子类如有需要（如维护持久连接），应重写此方法。
        默认实现不做任何操作。
        """
        pass
