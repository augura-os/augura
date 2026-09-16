"""Unified response envelope (contract §3)."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

DataT = TypeVar("DataT")


class Envelope(BaseModel, Generic[DataT]):
    success: bool
    data: DataT | None
    message: str


def ok(data: DataT | None = None, message: str = "") -> Envelope[DataT]:
    return Envelope(success=True, data=data, message=message)
