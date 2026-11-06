"""Bounded planning inputs. The application currently uses Shanghai civil time."""
from datetime import date, datetime, timedelta, timezone
import re
from typing import Literal

from pydantic import Field, StrictBool, field_validator, model_validator

from app.arrangements.schemas import Input


class Confirmed(Input):
    confirmed: StrictBool

    @field_validator("confirmed")
    @classmethod
    def explicit(cls, value):
        if value is not True:
            raise ValueError("请明确确认后保存")
        return value


def minute(value):
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d|24:00", value):
        raise ValueError("时间格式应为 HH:MM")
    h, m = map(int, value.split(":"))
    return h * 60 + m


def aware(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须包含时区")
    value = value.astimezone(timezone.utc)
    if value.second or value.microsecond:
        raise ValueError("请按整分钟设置安排")
    return value


class WorkingWindow(Input):
    weekday: int = Field(ge=0, le=6)
    start: str
    end: str

    @model_validator(mode="after")
    def valid(self):
        if minute(self.start) >= minute(self.end):
            raise ValueError("可用时段结束时间必须晚于开始时间；跨午夜请分两天填写")
        return self


class Preferences(Confirmed):
    version: int = Field(ge=0)
    tz: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    weekly: list[WorkingWindow] = Field(max_length=42)
    daily_limit_minutes: int = Field(ge=15, le=1440)
    buffer_minutes: int = Field(ge=0, le=120)

    @model_validator(mode="after")
    def nonoverlapping(self):
        for day in range(7):
            windows = sorted((minute(w.start), minute(w.end)) for w in self.weekly if w.weekday == day)
            if any(a[1] > b[0] for a, b in zip(windows, windows[1:])):
                raise ValueError("同一天的可用时段不能重叠")
        return self


class EventFields(Confirmed):
    title: str = Field(min_length=1, max_length=200)
    start_at: datetime
    end_at: datetime
    note: str = Field(default="", max_length=1000)

    _times = field_validator("start_at", "end_at")(aware)

    @model_validator(mode="after")
    def interval(self):
        if not self.start_at < self.end_at or (self.end_at - self.start_at).days > 31:
            raise ValueError("固定安排起止时间无效，单条最多 31 天")
        return self


class EventUpdate(EventFields):
    version: int = Field(ge=1)


class EventCreate(EventFields):
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class VersionAction(Confirmed):
    version: int = Field(ge=1)
    reason: str = Field(default="", max_length=300)


class Timing(Confirmed):
    version: int = Field(ge=0)
    task_version: int = Field(ge=1)
    remaining_minutes: int | None = Field(default=None, ge=0, le=100000)
    splittable: StrictBool = False
    min_block_minutes: int = Field(default=30, ge=5, le=480)
    dependency_ids: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("dependency_ids")
    @classmethod
    def unique(cls, value):
        if len(set(value)) != len(value) or any(not re.fullmatch(r"[a-f0-9]{32}", v) for v in value):
            raise ValueError("前置任务列表格式无效或含重复项")
        return value


class PlanInput(Input):
    start_date: date
    end_date: date
    task_ids: list[str] | None = Field(default=None, min_length=1, max_length=500)
    replace_unlocked: StrictBool = False

    @model_validator(mode="after")
    def bounded(self):
        if not 0 <= (self.end_date - self.start_date).days <= 30:
            raise ValueError("每次可安排 1 至 31 天")
        if self.task_ids is not None and len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("任务列表不能重复")
        return self


class MoveInput(Input):
    version: int = Field(ge=1)
    start_at: datetime
    end_at: datetime
    unlock_confirmed: StrictBool = False
    reason: str = Field(min_length=1, max_length=300)

    _times = field_validator("start_at", "end_at")(aware)

    @model_validator(mode="after")
    def interval(self):
        if not self.start_at < self.end_at or self.end_at - self.start_at > timedelta(days=1):
            raise ValueError("时间块起止时间无效")
        return self


class LockInput(VersionAction):
    locked: StrictBool
