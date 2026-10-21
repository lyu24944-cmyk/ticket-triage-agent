"""P6：工单检索改调 citation-rag 时的引用形状。"""

import json

import httpx

from ticket_triage.config import settings
from ticket_triage.tools import gateway
from ticket_triage.tools.rag_client import clear_seed_cache, search_knowledge_base


def _transport(handler):
    return httpx.MockTransport(handler)


def test_refund_maps_doc_id_and_excerpt() -> None:
    clear_seed_cache()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/documents":
            return httpx.Response(200, json={"doc_id": "x", "chunk_count": 1})
        body = json.loads(request.content.decode("utf-8"))
        assert "refund" in body["question"]
        return httpx.Response(
            200,
            json={
                "question": body["question"],
                "answer": "根据原文",
                "abstained": False,
                "citations": [
                    {
                        "chunk_id": "kb-billing-refund#c0",
                        "doc_id": "kb-billing-refund",
                        "title": "退款政策",
                        "excerpt": "14 天内退款。",
                        "score": 0.62,
                    }
                ],
            },
        )

    client = httpx.Client(transport=_transport(handler), base_url="http://rag.test")
    hits = search_knowledge_base("need a refund", client=client)
    assert hits == [{"id": "kb-billing-refund", "title": "退款政策", "body": "14 天内退款。"}]
    assert seen.count("/documents") == 3
    assert seen.count("/ask") == 1


def test_abstain_returns_no_citations() -> None:
    clear_seed_cache()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/documents":
            return httpx.Response(200, json={"doc_id": "x", "chunk_count": 1})
        return httpx.Response(
            200,
            json={
                "question": "hello",
                "answer": "知识库里没有足够依据回答这个问题。",
                "abstained": True,
                "citations": [],
            },
        )

    client = httpx.Client(transport=_transport(handler), base_url="http://rag.test")
    assert search_knowledge_base("hello", client=client) == []


def test_blank_query_does_not_call_service() -> None:
    clear_seed_cache()

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(request.url.path)

    client = httpx.Client(transport=_transport(handler), base_url="http://rag.test")
    assert search_knowledge_base("  ", client=client) == []


def test_gateway_uses_rag_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "use_mcp", False)
    monkeypatch.setattr(settings, "rag_base_url", "http://127.0.0.1:8001")

    def fake(query: str, limit: int = 3) -> list[dict[str, str]]:
        assert "退款" in query
        return [{"id": "kb-billing-refund", "title": "退款政策", "body": "14 天"}]

    monkeypatch.setattr("ticket_triage.tools.rag_client.search_knowledge_base", fake)
    hits = gateway.search_knowledge_base("紧急退款")
    assert hits[0]["id"] == "kb-billing-refund"
