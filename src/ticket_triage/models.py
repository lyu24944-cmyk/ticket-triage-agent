import json
import math
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

# 编号只允许安全字符，避免把路径或超长字符串写进 SQLite 和提示词。
TICKET_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class Priority(str, Enum):
    """工单优先级。"""

    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class Category(str, Enum):
    """工单类别。"""

    billing = "billing"
    technical = "technical"
    account = "account"
    other = "other"


def _reject_controls(value: str, *, allow_newlines: bool) -> str:
    if _CONTROL.search(value):
        raise ValueError("contains control characters")
    if not allow_newlines and any(char in value for char in "\r\n"):
        raise ValueError("contains a newline")
    return value


def clean_reviewer(value: str) -> str:
    """审核人姓名：去掉首尾空白，拒绝控制字符。"""
    text = value.strip()
    if not text or len(text) > 64:
        raise ValueError("reviewer is invalid")
    return _reject_controls(text, allow_newlines=False)


class TicketIn(BaseModel):
    """入站工单。"""

    ticket_id: str = Field(min_length=1, max_length=64, pattern=TICKET_ID_PATTERN)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)
    customer_email: str | None = Field(default=None, max_length=254)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("subject")
    @classmethod
    def subject_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("subject is empty")
        return _reject_controls(text, allow_newlines=False)

    @field_validator("body")
    @classmethod
    def body_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("body is empty")
        return _reject_controls(text, allow_newlines=True)

    @field_validator("customer_email")
    @classmethod
    def email_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        if _CONTROL.search(text) or any(char in text for char in "\r\n\t "):
            raise ValueError("email is invalid")
        if text.count("@") != 1:
            raise ValueError("email is invalid")
        return text

    @field_validator("metadata")
    @classmethod
    def metadata_bounds(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 16:
            raise ValueError("metadata has too many keys")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 40 or _CONTROL.search(key):
                raise ValueError("metadata key is invalid")
            if isinstance(item, bool) or item is None or isinstance(item, int):
                continue
            if isinstance(item, float):
                if math.isnan(item) or math.isinf(item):
                    raise ValueError("metadata value is invalid")
                continue
            if isinstance(item, str):
                if len(item) > 200 or _CONTROL.search(item):
                    raise ValueError("metadata value is invalid")
                continue
            raise ValueError("metadata values must be scalar")
        if len(json.dumps(value, ensure_ascii=False)) > 2000:
            raise ValueError("metadata is too large")
        return value


class Classification(BaseModel):
    """分类结果。"""

    priority: Priority
    category: Category
    rationale: str
    needs_escalation: bool = False


class DraftReply(BaseModel):
    """回复草稿。"""

    body: str
    citations: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class RunRequest(BaseModel):
    """发起一次分流运行。"""

    ticket: TicketIn
    auto_approve: bool = False


class RunResult(BaseModel):
    """运行结果。"""

    ticket_id: str
    status: str
    classification: Classification | None = None
    draft: DraftReply | None = None
    awaiting_human: bool = False
    errors: list[str] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    trace_id: str | None = None


class HumanDecision(BaseModel):
    """人工审核决策。"""

    ticket_id: str = Field(min_length=1, max_length=64, pattern=TICKET_ID_PATTERN)
    approved: bool
    reviewer: str = Field(min_length=1, max_length=64)
    edits: str | None = Field(default=None, max_length=4000)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("reviewer")
    @classmethod
    def reviewer_name(cls, value: str) -> str:
        return clean_reviewer(value)

    @field_validator("edits", "note")
    @classmethod
    def optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _reject_controls(value, allow_newlines=True)


class ReopenRequest(BaseModel):
    """把已处理工单送回待审。"""

    reviewer: str = Field(min_length=1, max_length=64)

    @field_validator("reviewer")
    @classmethod
    def reviewer_name(cls, value: str) -> str:
        return clean_reviewer(value)
