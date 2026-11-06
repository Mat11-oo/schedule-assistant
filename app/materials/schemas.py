"""Strict, bounded media and evidence contracts."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from app.arrangements.schemas import Input


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Evidence(Strict):
    unit_id: str = Field(min_length=1, max_length=32)
    quote: str = Field(min_length=1, max_length=2000)


class Finding(Strict):
    text: str = Field(min_length=1, max_length=1000)
    evidence: list[Evidence] = Field(min_length=1, max_length=6)
    origin: Literal["source", "analysis"] = "source"


class DataPoint(Strict):
    label: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=200)
    unit: str = Field(max_length=100)
    period: str = Field(max_length=200)
    scope: str = Field(max_length=300)
    evidence: list[Evidence] = Field(min_length=1, max_length=6)


class Action(Strict):
    title: str = Field(min_length=1, max_length=200)
    owner: Literal["self", "other", "unknown", "reference"]
    assignee: str = Field(max_length=100)
    deadline_text: str = Field(max_length=200)
    evidence: list[Evidence] = Field(min_length=1, max_length=6)


class Reading(Strict):
    conclusions: list[Finding] = Field(max_length=15)
    risks: list[Finding] = Field(max_length=15)
    key_data: list[DataPoint] = Field(max_length=25)
    topics: list[Finding] = Field(max_length=15)
    decisions: list[Finding] = Field(max_length=15)
    open_questions: list[Finding] = Field(max_length=15)
    actions: list[Action] = Field(max_length=25)


class Digest(Strict):
    overview: list[Finding] = Field(max_length=12)


class Answer(Strict):
    answer: str = Field(min_length=1, max_length=4000)
    evidence: list[Evidence] = Field(max_length=12)
    missing: str = Field(max_length=1000)


class OCRTable(Strict):
    rows: list[list[str]] = Field(max_length=200)


class OCRResult(Strict):
    text: str = Field(max_length=30000)
    tables: list[OCRTable] = Field(max_length=20)
    uncertain: list[str] = Field(max_length=50)


class WebInput(Input):
    title: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2000)
    allow_cloud: StrictBool = False


class RunInput(Input):
    allow_cloud: StrictBool = False
    acknowledge_uncertain: StrictBool = False


class SegmentEdit(Strict):
    unit_id: str = Field(min_length=1, max_length=32)
    text: str = Field(min_length=1, max_length=4000)
    speaker: str = Field(max_length=100)


class RevisionInput(Input):
    version: int = Field(ge=1)
    edits: list[SegmentEdit] = Field(default_factory=list, max_length=500)
    speaker_names: dict[str, str] = Field(default_factory=dict, max_length=100)
    self_speakers: list[str] = Field(default_factory=list, max_length=100)
    confirmed: StrictBool


class QuestionInput(Input):
    question: str = Field(min_length=1, max_length=2000)
    allow_cloud: StrictBool = False
    request_id: str = Field(min_length=8, max_length=64)


class DeleteInput(Input):
    confirmed: StrictBool
    @field_validator("confirmed")
    @classmethod
    def must_confirm(cls, value):
        if value is not True: raise ValueError("需要明确确认删除")
        return value
