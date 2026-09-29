"""Analysis job shapes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class JobInfo(BaseModel):
    id: str
    asset_id: str
    status: str
    attempt: int
    stage: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    available_at: datetime
