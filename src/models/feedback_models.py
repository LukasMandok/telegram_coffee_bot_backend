from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from beanie import Document
from pydantic import BaseModel, Field

from .beanie_models import TelegramUser
from ..utils.typing_utils import Link


class FeedbackType(str, Enum):
    BUG = "bug"
    FEATURE_REQUEST = "feature request"
    GENERAL = "general"


class FeedbackStatus(str, Enum):
    OPEN = "open"
    MODIFIED = "modified"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"
    POSTPONED = "postponed"


class FeedbackComment(BaseModel):
    author_user_id: int
    author_display_name: str
    author_is_admin: bool

    message: str = Field(..., min_length=1, max_length=1500)
    created_at: datetime = Field(default_factory=datetime.now)

    # Whether the *other party* (submitter vs admin) has viewed this comment.
    viewed: bool = Field(default=False)


class Feedback(Document):
    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)

    status: FeedbackStatus = Field(default=FeedbackStatus.OPEN)
    submitter: Link[TelegramUser]

    # 1..5 (5=high). For 'general' feedback we store None.
    priority: Optional[int] = Field(default=None, ge=1, le=5)

    type: FeedbackType

    comments: List[FeedbackComment] = Field(default_factory=list)

    # Field-level update markers (used instead of "modified" status).
    title_updated: bool = Field(default=False)
    description_updated: bool = Field(default=False)

    # Admin inbox marker: new feedback starts as not viewed by admins.
    # Default True so existing documents (without this field) don't all show as "new".
    viewed_by_admin: bool = Field(default=True)

    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    class Settings:
        name = "feedback"
