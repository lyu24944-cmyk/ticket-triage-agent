from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


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


class TicketIn(BaseModel):
    """入站工单。"""

    ticket_id: str
    subject: str
    body: str
    customer_email: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


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
    trace_id: str | None = None


class HumanDecision(BaseModel):
    """人工审核决策。"""

    ticket_id: str
    approved: bool
    edits: str | None = None
    note: str | None = None
