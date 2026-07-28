"""图片搜索服务.

协调各搜图策略，并行执行搜索并聚合结果。
"""

from __future__ import annotations

import asyncio
import time

from astrbot.api import logger

from .constant import STRATEGY_ALIAS_MAP
from .models import ExplorationResult, SearchResultItem
from .strategy import ImageSearchStrategy
from .utils import download_bytes


class ImgExplorationService:
    """图片搜索服务.

    负责协调多个搜图策略并行执行，聚合结果并下载缩略图。
    """

    def __init__(self, strategies: list[ImageSearchStrategy]) -> None:
        """初始化搜索服务.

        Args:
            strategies: 搜图策略列表
        """
        self.strategies = strategies
        # 建立策略名称索引
        self._strategy_map: dict[str, ImageSearchStrategy] = {}
        for strategy in self.strategies:
            name = strategy.get_service_name().lower()
            self._strategy_map[name] = strategy

    def get_available_strategies(self) -> list[str]:
        """获取当前可用的策略名称列表.

        Returns:
            策略名称列表
        """
        return [s.get_service_name() for s in self.strategies]

    def resolve_strategy_names(
        self, names: list[str] | None
    ) -> tuple[list[ImageSearchStrategy], list[str]]:
        """解析策略名称别名，返回对应的策略实例.

        Args:
            names: 策略名称或别名列表，None 或空列表表示使用全部策略

        Returns:
            (匹配的策略实例列表, 未找到的策略名称列表)
        """
        if not names:
            return self.strategies, []

        resolved: list[ImageSearchStrategy] = []
        not_found: list[str] = []

        for name in names:
            name_lower = name.lower().strip()
            # 先通过别名映射
            canonical_name = STRATEGY_ALIAS_MAP.get(name_lower, name_lower)
            # 查找策略
            strategy = self._strategy_map.get(canonical_name.lower())
            if strategy:
                # 已找到策略，避免重复添加
                if strategy not in resolved:
                    resolved.append(strategy)
                # 如果是重复输入，则忽略，不计入 not_found
            else:
                # 未找到对应策略，记录到 not_found 并打印日志
                not_found.append(name)
                logger.warning(f"[ImgExploration] 未找到策略 '{name}'")

        return resolved, not_found

    async def explore(
        self, image_url: str, strategy_names: list[str] | None = None
    ) -> ExplorationResult:
        """执行图片搜索.

        Args:
            image_url: 图片 URL 地址
            strategy_names: 指定使用的策略名称列表，None 表示使用所有策略

        Returns:
            包含所有搜索结果的 ExplorationResult
        """
        # 解析要使用的策略
        strategies_to_use, not_found = self.resolve_strategy_names(strategy_names)

        # 如果指定了策略但全部未找到，返回空结果并记录错误
        if strategy_names and not strategies_to_use:
            available = self.get_available_strategies()
            logger.warning(
                f"[ImgExploration] 指定的策略 {strategy_names} 全部不可用，"
                f"当前可用策略: {available}"
            )
            return ExplorationResult()

        if not strategies_to_use:
            logger.warning("[ImgExploration] 未找到任何可用的搜图策略")
            return ExplorationResult()

        start_time = time.monotonic()
        strategy_names_str = ", ".join(s.get_service_name() for s in strategies_to_use)
        if not_found:
            logger.warning(f"[ImgExploration] 以下策略未找到: {not_found}")
        logger.info(
            f"[ImgExploration] 开始搜图，目标 URL: {image_url}, 使用策略: {strategy_names_str}"
        )

        try:
            # 并行调用所有策略
            tasks = [strategy.search(image_url) for strategy in strategies_to_use]
            results_list = await asyncio.gather(*tasks, return_exceptions=True)

            # 聚合结果
            all_items: list[SearchResultItem] = []
            for i, result in enumerate(results_list):
                if isinstance(result, Exception):
                    logger.error(
                        f"[ImgExploration] 策略 [{strategies_to_use[i].get_service_name()}] 执行失败: {result}"
                    )
                elif isinstance(result, list):
                    all_items.extend(result)

            logger.info(
                f"[ImgExploration] 搜索完成，共获取 {len(all_items)} 条结果，开始下载缩略图..."
            )

            # 并行下载缩略图
            await self._fill_thumbnails(all_items)

            elapsed = time.monotonic() - start_time
            logger.info(f"[ImgExploration] 任务结束，总耗时: {elapsed:.2f}s")

            return ExplorationResult(items=all_items)

        except Exception as e:
            logger.error(f"[ImgExploration] 搜索主流程异常: {e}")
            return ExplorationResult()

    @staticmethod
    async def _fill_thumbnails(items: list[SearchResultItem]) -> None:
        """并行下载缩略图并回填到结果项中.

        Args:
            items: 搜索结果列表
        """
        # 找出需要下载缩略图的项
        download_tasks = []
        indices = []

        for i, item in enumerate(items):
            # 如果已有缩略图字节或没有缩略图 URL，跳过
            if item.thumbnail_bytes is not None or not item.thumbnail:
                continue

            indices.append(i)
            download_tasks.append(download_bytes(item.thumbnail))

        if not download_tasks:
            return

        # 并行下载
        results = await asyncio.gather(*download_tasks, return_exceptions=False)

        # 回填缩略图字节
        for idx, bytes_data in zip(indices, results, strict=True):
            if bytes_data:
                items[idx] = items[idx].with_thumbnail_bytes(bytes_data)
