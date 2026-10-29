"""送进分类、调度和起草的上下文。

只保留四样东西：当前工单、已经成功的工具结果、这一次检索命中的文档、上一步错误码。
超出 token 上限时，先删最早的工具结果，工单和本次引用留到最后。
"""

from __future__ import annotations

from dataclasses import dataclass

from ticket_triage.config import settings
from ticket_triage.models import Classification, TicketIn
from ticket_triage.privacy import history_for_model, kb_hits_for_model, ticket_for_model


@dataclass
class _Block:
    kind: str
    text: str
    short: str = ""
    full: bool = True


def estimate_tokens(text: str) -> int:
    """粗算 token：一个非 ASCII 字符算 1，四个 ASCII 字符算 1。不是模型分词器。"""
    tokens = 0
    ascii_run = 0
    for char in text:
        if ord(char) < 128:
            ascii_run += 1
            if ascii_run == 4:
                tokens += 1
                ascii_run = 0
        else:
            if ascii_run:
                tokens += 1
                ascii_run = 0
            tokens += 1
    if ascii_run:
        tokens += 1
    return tokens


def error_code(raw: object) -> str | None:
    """只留错误码。工具异常后面的长文本不进提示词。"""
    text = str(raw or "").strip()
    if not text:
        return None
    line = text.splitlines()[0].strip()
    if line.startswith("enrich_failed"):
        return "enrich_failed"
    if line.startswith("act_model_failed"):
        return "act_model_failed"
    return line.split(" ", 1)[0][:120]


def previous_error(tool_calls: list[dict] | None) -> str | None:
    """只取紧挨着的上一步。上一步成功时，更早的失败不再送进模型。"""
    if not tool_calls:
        return None
    last = tool_calls[-1]
    if last.get("ok"):
        return None
    return error_code(last.get("error"))


def _ticket_text(lines: list[str]) -> str:
    return "\n".join(lines)


def _shorten_ticket(text: str) -> str:
    """从正文末尾删掉一段。主题行保持不动。"""
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if line.startswith(("正文：", "工单正文：")):
            prefix, _, body = line.partition("：")
            if len(body) <= 1:
                lines[index] = f"{prefix}："
                return "\n".join(lines)
            keep = max(1, len(body) // 2)
            lines[index] = f"{prefix}：{body[:keep]}"
            return "\n".join(lines)
    return text


def _citation(hit: dict, *, full: bool) -> str:
    ident = str(hit.get("id") or "")
    title = str(hit.get("title") or "")
    if not full:
        return f"- [{ident}] {title}"
    body = str(hit.get("body") or "")
    return f"- [{ident}] {title}：{body}"


def _tool_lines(tool_calls: list[dict] | None, history: list | None) -> list[str]:
    lines: list[str] = []
    for call in tool_calls or []:
        if not call.get("ok") or call.get("name") in {None, "finish"}:
            continue
        name = str(call.get("name"))
        arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        if name == "search_knowledge_base":
            query = str(arguments.get("query") or "")
            lines.append(f"工具结果 search_knowledge_base：query={query}")
        elif name == "get_ticket_history":
            statuses = [
                str(item.get("status"))
                for item in (history or [])
                if isinstance(item, dict) and item.get("status")
            ]
            summary = ",".join(statuses) if statuses else "无"
            lines.append(f"工具结果 get_ticket_history：{summary}")
        else:
            lines.append(f"工具结果 {name}")
    return lines


def _render(blocks: list[_Block]) -> str:
    return "\n".join(block.text for block in blocks if block.text)


def _over(blocks: list[_Block], budget: int) -> bool:
    return estimate_tokens(_render(blocks)) > budget


def _drop_first(blocks: list[_Block], kind: str) -> bool:
    for index, block in enumerate(blocks):
        if block.kind == kind:
            del blocks[index]
            return True
    return False


def fit(blocks: list[_Block], budget: int | None = None) -> str:
    """按保留顺序裁到预算内。预算用配置里的 context_token_budget。"""
    limit = settings.context_token_budget if budget is None else budget
    kept = [
        _Block(kind=block.kind, text=block.text, short=block.short, full=block.full)
        for block in blocks
    ]
    while kept and _over(kept, limit) and _drop_first(kept, "tool"):
        pass
    while kept and _over(kept, limit) and _drop_first(kept, "error"):
        pass
    shrunk = True
    while kept and _over(kept, limit) and shrunk:
        shrunk = False
        for block in kept:
            if block.kind == "citation" and block.full and block.short:
                block.text = block.short
                block.full = False
                shrunk = True
                break
    while kept and _over(kept, limit) and _drop_first(kept, "citation"):
        pass
    while kept and _over(kept, limit):
        ticket = next((block for block in kept if block.kind == "ticket"), None)
        if ticket is None:
            break
        shortened = _shorten_ticket(ticket.text)
        if shortened == ticket.text:
            break
        ticket.text = shortened
    return _render(kept)


def _budget(budget: int | None) -> int | None:
    return budget


def classify_user(ticket: TicketIn, *, budget: int | None = None) -> str:
    safe = ticket_for_model(ticket)
    block = _Block(
        kind="ticket",
        text=_ticket_text(
            [
                f"工单主题：{safe.subject}",
                f"工单正文：{safe.body}",
            ]
        ),
    )
    return fit([block], _budget(budget))


def act_user(
    ticket: TicketIn,
    classification: Classification | None,
    tool_calls: list[dict] | None,
    kb_hits: list[dict] | None = None,
    history: list | None = None,
    *,
    budget: int | None = None,
) -> str:
    safe = ticket_for_model(ticket)
    safe_history = history_for_model(history)
    safe_hits = kb_hits_for_model(kb_hits)
    category = classification.category.value if classification else "unknown"
    blocks = [
        _Block(
            kind="ticket",
            text=_ticket_text(
                [
                    f"工单编号：{safe.ticket_id}",
                    f"主题：{safe.subject}",
                    f"正文：{safe.body}",
                    f"类别：{category}",
                ]
            ),
        )
    ]
    for line in _tool_lines(tool_calls, safe_history):
        blocks.append(_Block(kind="tool", text=line))
    for hit in safe_hits:
        blocks.append(
            _Block(
                kind="citation",
                text=_citation(hit, full=True),
                short=_citation(hit, full=False),
            )
        )
    code = previous_error(tool_calls)
    if code:
        blocks.append(_Block(kind="error", text=f"上一步错误：{code}"))
    return fit(blocks, _budget(budget))


def draft_user(
    ticket: TicketIn,
    classification: Classification,
    kb_hits: list[dict] | None,
    tool_calls: list[dict] | None = None,
    history: list | None = None,
    *,
    budget: int | None = None,
) -> str:
    safe = ticket_for_model(ticket)
    safe_history = history_for_model(history)
    safe_hits = kb_hits_for_model(kb_hits)
    blocks = [
        _Block(
            kind="ticket",
            text=_ticket_text(
                [
                    f"工单主题：{safe.subject}",
                    f"工单正文：{safe.body}",
                    (
                        f"类别：{classification.category.value}，"
                        f"优先级：{classification.priority.value}"
                    ),
                ]
            ),
        )
    ]
    for line in _tool_lines(tool_calls, safe_history):
        blocks.append(_Block(kind="tool", text=line))
    if safe_hits:
        for hit in safe_hits:
            blocks.append(
                _Block(
                    kind="citation",
                    text=_citation(hit, full=True),
                    short=_citation(hit, full=False),
                )
            )
    else:
        blocks.append(_Block(kind="citation", text="可引用的知识库文档：无", short="可引用的知识库文档：无"))
    code = previous_error(tool_calls)
    if code:
        blocks.append(_Block(kind="error", text=f"上一步错误：{code}"))
    return fit(blocks, _budget(budget))
