"""Creative DTOs — lifecycle（PUT /creatives/{id}/lifecycle）."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

LifecycleState = Literal["active", "watch", "archived"]


class LifecycleUpdate(BaseModel):
    state: LifecycleState
    reason: str = ""
