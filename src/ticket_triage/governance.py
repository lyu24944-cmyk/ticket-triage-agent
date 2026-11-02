"""只读模型治理视图：当前销钉和退回信号口径。

不含密钥、不含工单正文。不切换销钉，不跑评测。
"""

from __future__ import annotations

from typing import Any

from ticket_triage.config import settings
from ticket_triage.llm import ModelLLM, get_llm
from ticket_triage.llm.prompts import prompt_tag, resolve_prompt_rev
from ticket_triage.ops_log import STUB_FALLBACK_PREFIXES

_KNOWN_PROVIDERS = frozenset({"stub", "openai", "anthropic"})

_SIGNAL_COPY: dict[str, dict[str, str]] = {
    "classify_model_failed": {
        "label": "分类模型失败",
        "node": "classify",
        "effect": "已退回关键词分类，流程通常继续。",
    },
    "act_model_failed": {
        "label": "调度模型失败",
        "node": "act",
        "effect": "已改走固定规则选工具。工具白名单错误是 tool_not_allowed，不是这条。",
    },
    "draft_model_failed": {
        "label": "草稿模型失败",
        "node": "draft",
        "effect": "已退回 stub 草稿，通常仍停在待审。",
    },
}


def model_governance_status() -> dict[str, Any]:
    """给审核台和值班看的固定字段。未知销钉不回显原文。"""
    pin_status = "ok"
    rev: str | None = None
    canonical: str | None = None
    try:
        rev = resolve_prompt_rev()
        canonical = "current" if rev == "v3" else "previous"
    except ValueError:
        pin_status = "unknown"

    raw_provider = (settings.llm_provider or "stub").strip().lower()
    provider = raw_provider if raw_provider in _KNOWN_PROVIDERS else "other"
    uses_model = isinstance(get_llm(), ModelLLM)
    version = (
        prompt_tag(provider=raw_provider if provider != "other" else "stub", uses_model=uses_model, rev=rev)
        if rev is not None
        else None
    )
    if uses_model:
        path_note = "当前会调用模型。出现下列失败码表示这一步已退回 stub。"
    else:
        path_note = "当前是 stub。这些失败码只在模型调用失败后退回时出现，stub 本身不产生它们。"

    signals = []
    for code in STUB_FALLBACK_PREFIXES:
        meta = _SIGNAL_COPY[code]
        signals.append(
            {
                "code": code,
                "label": meta["label"],
                "node": meta["node"],
                "effect": meta["effect"],
                "log": f"node={meta['node']} stub_fallback=1 model_failed={code}",
            }
        )

    return {
        "readonly": True,
        "prompt_pin": canonical,
        "prompt_rev": rev,
        "pin_status": pin_status,
        "prompt_version": version,
        "llm_provider": provider,
        "uses_model": uses_model,
        "path_note": path_note,
        "rollback": (
            "退回变多或工具选择下降时，把 PROMPT_PIN 设为 previous，"
            "只重启这一个 API 进程。不要求再训练。"
        ),
        "ci": "CI 只跑 stub 与当前销钉，不把外网模型对比打进合并门槛。",
        "signals": signals,
    }
