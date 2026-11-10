"""Read-only proxy for the shared, public Cloud API announcement feed."""

from typing import Any

import requests
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError

from .auth import require_user
from .cloud_client import load_cloud_config


router = APIRouter(prefix="/api/announcements", tags=["announcements"])


class Announcement(BaseModel):
    id: str | int
    title: str = Field(max_length=300)
    body: str = Field(max_length=20000)
    pinned: bool = False
    revision: int = Field(ge=1)
    updated_at: str | None = None
    published_at: str | None = None


class AnnouncementPage(BaseModel):
    items: list[Announcement] = Field(max_length=100)
    total: int = Field(ge=0)


@router.get("")
def list_announcements(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    require_user(request)
    config = load_cloud_config()
    if not config.configured:
        raise HTTPException(status_code=503, detail="公告服务尚未配置")
    try:
        # This endpoint is public; never forward local cookies or cloud tokens.
        with requests.get(
            f"{config.base_url}/announcements",
            params={"limit": limit, "offset": offset},
            headers={"Accept": "application/json"},
            timeout=(5, 10),
            allow_redirects=False,
        ) as response:
            if response.status_code != 200:
                raise HTTPException(status_code=502, detail="公告暂时无法加载，请稍后重试")
            return AnnouncementPage.model_validate(response.json()).model_dump()
    except (requests.RequestException, ValueError, ValidationError) as exc:
        raise HTTPException(status_code=502, detail="公告暂时无法加载，请稍后重试") from exc
