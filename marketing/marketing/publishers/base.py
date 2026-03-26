"""BasePublisher — 平台发布抽象基类"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from marketing.logger import get_logger

log = get_logger("publisher")


@dataclass
class PublishResult:
    success: bool
    platform: str
    publish_url: str = ""
    article_id: str = ""
    error: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContentPayload:
    """待发布的内容"""
    content_id: int
    title: str
    body: str
    tags: list[str]
    images: list[str]  # 截图文件路径
    platform: str
    novel_title: str


class BasePublisher(ABC):
    """平台发布器抽象基类"""

    platform: str = ""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    @abstractmethod
    async def validate_credentials(self) -> bool:
        """验证凭证有效性"""
        ...

    @abstractmethod
    async def publish(self, content: ContentPayload) -> PublishResult:
        """发布内容到平台"""
        ...

    async def dry_run(self, content: ContentPayload) -> str:
        """模拟发布，返回预览信息"""
        return (
            f"[DRY RUN] {self.platform}\n"
            f"标题: {content.title}\n"
            f"正文: {content.body[:100]}...\n"
            f"标签: {', '.join(content.tags)}\n"
            f"图片: {len(content.images)} 张"
        )

    async def publish_with_retry(
        self,
        content: ContentPayload,
        max_retries: int = 3,
    ) -> PublishResult:
        """带指数退避重试的发布"""
        for attempt in range(max_retries):
            try:
                result = await self.publish(content)
                if result.success:
                    return result
                # 非限流错误直接返回
                if "rate" not in result.error.lower() and "limit" not in result.error.lower():
                    return result
            except Exception as e:
                result = PublishResult(
                    success=False,
                    platform=self.platform,
                    error=str(e),
                )

            wait = 30 * (2 ** attempt)
            log.warning(
                "%s 发布失败 (attempt %d/%d): %s — %ds 后重试",
                self.platform, attempt + 1, max_retries, result.error, wait,
            )
            if attempt == max_retries - 1:
                return result
            await asyncio.sleep(wait)

        return PublishResult(
            success=False,
            platform=self.platform,
            error="超过最大重试次数",
        )
