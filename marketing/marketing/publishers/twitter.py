"""Twitter/X 发布模块 — OAuth 2.0 Bearer Token"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from marketing.logger import get_logger
from marketing.publishers.base import BasePublisher, ContentPayload, PublishResult

log = get_logger("publisher.twitter")


class TwitterPublisher(BasePublisher):
    platform = "twitter"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.bearer_token = config.get("bearer_token", "")
        self.api_key = config.get("api_key", "")
        self.api_secret = config.get("api_secret", "")
        self.access_token = config.get("access_token", "")
        self.access_secret = config.get("access_token_secret", "")

    async def validate_credentials(self) -> bool:
        if not self.bearer_token:
            log.error("Twitter Bearer Token 未配置")
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://api.twitter.com/2/users/me",
                    headers={"Authorization": f"Bearer {self.bearer_token}"},
                )
                if resp.status_code == 200:
                    name = resp.json().get("data", {}).get("username", "")
                    log.info("Twitter 凭证有效: @%s", name)
                    return True
                log.error("Twitter 凭证无效: %d", resp.status_code)
                return False
        except Exception as e:
            log.error("Twitter 凭证验证失败: %s", e)
            return False

    async def publish(self, content: ContentPayload) -> PublishResult:
        # Twitter 需要 OAuth 1.0a 签名，这里使用简化的 Bearer Token 方式
        # 完整实现需要 tweepy 或 OAuth 签名库
        headers = {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
        }

        tweet_text = content.body
        if len(tweet_text) > 280:
            tweet_text = tweet_text[:277] + "..."

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                # 上传图片（如果有）
                media_ids: list[str] = []
                for img_path in content.images[:4]:  # Twitter 最多 4 张图
                    if Path(img_path).exists():
                        mid = await self._upload_media(client, img_path)
                        if mid:
                            media_ids.append(mid)

                body: dict[str, Any] = {"text": tweet_text}
                if media_ids:
                    body["media"] = {"media_ids": media_ids}

                resp = await client.post(
                    "https://api.twitter.com/2/tweets",
                    headers=headers,
                    json=body,
                )

                if resp.status_code in (200, 201):
                    data = resp.json().get("data", {})
                    tweet_id = data.get("id", "")
                    url = f"https://twitter.com/i/status/{tweet_id}"
                    log.info("Twitter 发布成功: %s", url)
                    return PublishResult(
                        success=True,
                        platform=self.platform,
                        publish_url=url,
                        article_id=tweet_id,
                    )

                return PublishResult(
                    success=False,
                    platform=self.platform,
                    error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                )

        except Exception as e:
            return PublishResult(
                success=False,
                platform=self.platform,
                error=str(e),
            )

    async def _upload_media(
        self,
        client: httpx.AsyncClient,
        path: str,
    ) -> str | None:
        """上传媒体文件到 Twitter"""
        try:
            with open(path, "rb") as f:
                resp = await client.post(
                    "https://upload.twitter.com/1.1/media/upload.json",
                    headers={
                        "Authorization": f"Bearer {self.bearer_token}",
                    },
                    files={"media": f},
                )
                if resp.status_code == 200:
                    return resp.json().get("media_id_string")
        except Exception as e:
            log.warning("Twitter 图片上传失败 %s: %s", path, e)
        return None
