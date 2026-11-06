"""Explicit inputs for briefs, possible duplicates and source-backed changes."""
from datetime import datetime
from typing import Literal

from pydantic import Field, StrictBool, field_validator, model_validator

from app.arrangements.schemas import Input, TaskFields
from app.planning.schemas import Confirmed


class BriefPreferences(Confirmed):
    version: int = Field(ge=0)
    enabled: StrictBool = False
    at: str = Field(default="08:30", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    lookahead_days: int = Field(default=3, ge=1, le=14)
    sections: list[Literal["schedule", "due", "overdue", "waiting", "conflicts", "decisions"]] = Field(min_length=1, max_length=6)

    @field_validator("sections")
    @classmethod
    def unique(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("简报内容不能重复")
        return value


class Distinct(Confirmed):
    task_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class SourceChange(Input):
    task_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    task_version: int = Field(ge=1)
    title: str = Field(default="", max_length=200)
    due_at: datetime | None = None
    change_title: StrictBool = False
    change_due: StrictBool = False
    responsibility: Literal["keep_self", "wait_other", "cancel_self"] = "keep_self"
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("due_at")
    @classmethod
    def aware_due(cls, value):
        return TaskFields.aware_time(value)

    @model_validator(mode="after")
    def actual_change(self):
        if self.change_title and not self.title:
            raise ValueError("修改后的任务名称不能为空")
        if not (self.change_title or self.change_due or self.responsibility != "keep_self"):
            raise ValueError("请选择要修改的字段；只增加出处请使用关联任务")
        return self
