"""Bounded input contracts. Ownership is always injected by the session."""
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    user_id: str = ""  # Injected by SessionIdentityMiddleware, never used as authority.


class SourceInput(Input):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20000)
    kind: Literal["message", "document_text", "meeting_transcript"] = "message"
    original_at: datetime | None = None
    tz: str = "Asia/Shanghai"

    @field_validator("original_at")
    @classmethod
    def original_has_zone(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("来源时间必须包含时区")
        return value

    @field_validator("tz")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("请选择有效时区") from None
        return value


class CandidateInput(Input):
    title: str = Field(min_length=1, max_length=200)
    quote: str = Field(min_length=1, max_length=2000)
    owner: Literal["self", "other", "unknown", "reference"] = "unknown"
    deadline_text: str = Field(default="", max_length=200)


class ExtractedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    quote: str = Field(min_length=1, max_length=2000)
    owner: Literal["self", "other", "unknown", "reference"]
    deadline_text: str = Field(max_length=200)


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    summary: str = Field(max_length=2000)
    candidates: list[ExtractedCandidate] = Field(max_length=30)


class TaskFields(Input):
    title: str = Field(min_length=1, max_length=200)
    due_at: datetime | None = None
    estimated_minutes: int | None = Field(default=None, ge=1, le=100000)
    priority: Literal["high", "normal", "low"] = "normal"
    note: str = Field(default="", max_length=2000)
    reminder_at: datetime | None = None

    @field_validator("due_at", "reminder_at")
    @classmethod
    def aware_time(cls, value):
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("时间必须包含时区")
            return value.astimezone(timezone.utc)
        return value


class Confirmed(TaskFields):
    confirmed: StrictBool

    @field_validator("confirmed")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("需要明确确认")
        return value


class Decision(Confirmed):
    action: Literal["accept", "ignore", "reference", "link"]
    take_ownership: StrictBool = False
    link_task_id: str | None = Field(default=None, max_length=32)


class TaskUpdate(Confirmed):
    version: int = Field(ge=1)
    status: Literal["todo", "doing", "waiting", "done", "cancelled"]
