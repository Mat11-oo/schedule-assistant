"""Pydantic request/response models for the HTTP API."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ProfileBody(BaseModel):
    name: Optional[str] = None
    city: Optional[str] = None
    tz: Optional[str] = None
    occupation: Optional[str] = None
    interests: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    # Values the agent may use internally but must never output (masked everywhere).
    privacy_sandbox: list[str] = Field(default_factory=list)


class UpsertProfileRequest(BaseModel):
    user_id: str = "demo"
    profile: Optional[ProfileBody] = None


class DraftRequest(BaseModel):
    """Ask for a written draft (email / message / article) that needs confirmation."""

    user_id: str = "demo"
    thread_id: Optional[str] = None
    request_id: Optional[str] = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    request: str


class ConfirmRequest(BaseModel):
    user_id: str = "demo"
    thread_id: str
    confirmed: bool = True
    edits: Optional[dict[str, Any]] = None
    channel: str = "web"
    reason: Optional[str] = None  # reject reason → fed into the redraft
    confirm_token: Optional[str] = None  # one-time, owner-bound (POST /api/confirm/{thread_id}/token)


class ContinueTaskRequest(BaseModel):
    """User clicked "continue" on a budget card. User comes from the auth token."""

    thread_id: str
    user_id: Optional[str] = None


class ChatRequest(BaseModel):
    """High-level chat; drafting requests go through the confirm flow."""

    user_id: str = "demo"
    thread_id: Optional[str] = None
    request_id: Optional[str] = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    message: str
    # Set when the message answers a question card: {"ask_id", "question", "type", "prompt"}
    reply_to: Optional[dict[str, Any]] = None


class BindChannelRequest(BaseModel):
    channel: str  # feishu | openclaw_wechat
    external_user_id: str
    user_id: str
    display_name: str = ""


class ApiResponse(BaseModel):
    ok: bool = True
    thread_id: Optional[str] = None
    status: str = "ok"
    data: dict[str, Any] = Field(default_factory=dict)
    awaiting_confirm: bool = False
    error: Optional[str] = None
