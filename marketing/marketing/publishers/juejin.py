"""掘金发布模块 — Cookie 认证"""

from __future__ import annotations

from typing import Any

import httpx

from marketing.logger import get_logger
from marketing.publishers.base import BasePublisher, ContentPayload, PublishResult

log = get_logger("publisher.juejin")

_API_BASE = "https://api.juejin.cn/content_api/v1"


class JuejinPublisher(BasePublisher):
    platform = "juejin"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.cookie = config.get("cookie", "")
        self.category_id = config.get("category_id", "6809637767543259144")  # 前端

    async def validate_credentials(self) -> bool:
        if not self.cookie:
            log.error("掘金 Cookie 未配置")
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://api.juejin.cn/user_api/v1/user/get",
                    headers={"Cookie": self.cookie},
                )
                data = resp.json()
                if data.get("err_no") == 0:
                    name = data.get("data", {}).get("user_name", "")
                    log.info("掘金凭证有效: %s", name)
                    return True
                log.error("掘金凭证无效: %s", data.get("err_msg"))
                return False
        except Exception as e:
            log.error("掘金凭证验证失败: %s", e)
            return False

    async def publish(self, content: ContentPayload) -> PublishResult:
        headers = {
            "Cookie": self.cookie,
            "Content-Type": "application/json",
        }

        # 创建草稿
        draft_body = {
            "category_id": self.category_id,
            "tag_ids": [],
            "link_url": "",
            "cover_image": "",
            "title": content.title,
            "brief_content": content.body[:100],
            "edit_type": 10,  # markdown
            "html_content": "deprecated",
            "mark_content": content.body,
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                # 创建草稿
                resp = await client.post(
                    f"{_API_BASE}/article_draft/create",
                    headers=headers,
                    json=draft_body,
                )
                data = resp.json()
                if data.get("err_no") != 0:
                    return PublishResult(
                        success=False,
                        platform=self.platform,
                        error=f"创建草稿失败: {data.get('err_msg')}",
                    )

                draft_id = data["data"]["id"]

                # 发布草稿
                resp = await client.post(
                    f"{_API_BASE}/article/publish",
                    headers=headers,
                    json={"draft_id": draft_id, "column_ids": []},
                )
                data = resp.json()
                if data.get("err_no") != 0:
                    return PublishResult(
                        success=False,
                        platform=self.platform,
                        error=f"发布失败: {data.get('err_msg')}",
                    )

                article_id = data["data"]["article_id"]
                url = f"https://juejin.cn/post/{article_id}"

                log.info("掘金发布成功: %s", url)
                return PublishResult(
                    success=True,
                    platform=self.platform,
                    publish_url=url,
                    article_id=str(article_id),
                )

        except Exception as e:
            return PublishResult(
                success=False,
                platform=self.platform,
                error=str(e),
            )
