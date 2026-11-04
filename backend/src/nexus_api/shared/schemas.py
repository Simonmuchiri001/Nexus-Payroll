"""Shared API pagination and response models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Page[T](BaseModel):
    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)


class MessageResponse(BaseModel):
    message: str
