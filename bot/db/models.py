"""Data models for database entities."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Session:
    id: int
    user_id: int
    title: str
    model: Optional[str]
    is_active: bool
    summary: Optional[str]
    created_at: datetime
    updated_at: datetime


@dataclass
class Message:
    id: int
    session_id: int
    role: str  # 'user', 'assistant', 'system'
    content: str
    is_compressed: bool
    tokens: int
    created_at: datetime


@dataclass
class UserSetting:
    user_id: int
    selected_model: Optional[str]
    system_prompt: Optional[str]
    web_search_mode: str  # 'auto', 'on', 'off'
    created_at: datetime
    updated_at: datetime
