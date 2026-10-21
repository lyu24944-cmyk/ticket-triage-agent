"""LLM 适配层。"""

from ticket_triage.llm.model import ModelLLM
from ticket_triage.llm.stub import StubLLM, get_llm

__all__ = ["ModelLLM", "StubLLM", "get_llm"]
