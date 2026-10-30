"""本地 KB FTS5：短查询子串命中；无关文本仍为空。"""

from ticket_triage.tools.kb import search_knowledge_base
from ticket_triage.tools.tickets import seed_ticket, list_tickets


def test_fts_short_query_hits_body_policy_id():
    hits = search_knowledge_base("REF-14")
    assert [h["id"] for h in hits] == ["kb-billing-refund"]


def test_fts_chinese_substring_in_title():
    hits = search_knowledge_base("冷静期")
    assert [h["id"] for h in hits] == ["kb-account-delete"]


def test_keyword_path_still_handles_long_ticket_text():
    hits = search_knowledge_base("我被重复扣款，需要退款")
    assert hits and hits[0]["id"] == "kb-billing-refund"


def test_unrelated_still_empty():
    assert search_knowledge_base("今天天气不错") == []
    assert search_knowledge_base("") == []


def test_ticket_list_uses_fts_after_seed():
    seed_ticket("t-fts-1", "无法登录系统", "密码重置失败多次")
    seed_ticket("t-fts-2", "发票问题", "需要开具发票")
    found = list_tickets(q="密码重置")
    assert [t["ticket_id"] for t in found] == ["t-fts-1"]
    assert list_tickets(q="不存在的词xyz") == []
