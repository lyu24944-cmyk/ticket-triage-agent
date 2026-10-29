"""P6 演示：问答服务已启动时，退款工单带引用，闲聊不带引用。"""

from __future__ import annotations

import os
import sys

from ticket_triage.graph.workflow import run_triage
from ticket_triage.models import RunRequest, TicketIn


def _run(ticket_id: str, subject: str, body: str):
    return run_triage(
        RunRequest(
            ticket=TicketIn(ticket_id=ticket_id, subject=subject, body=body),
        )
    )


def main() -> int:
    if not os.environ.get("RAG_BASE_URL"):
        print("Set RAG_BASE_URL, for example http://127.0.0.1:8001")
        return 1
    refund = _run("p6-demo-refund", "紧急退款", "我被重复扣款，需要退款")
    weather = _run("p6-demo-weather", "今天天气", "出门散步心情不错")
    refund_ids = list(refund.draft.citations) if refund.draft else []
    weather_ids = list(weather.draft.citations) if weather.draft else []
    print(f"refund status={refund.status} citations={refund_ids}")
    print(f"weather status={weather.status} citations={weather_ids}")
    if not any(item.startswith("kb-billing") for item in refund_ids):
        print("expected a kb-billing citation")
        return 1
    if weather_ids:
        print("weather ticket must not cite")
        return 1
    print("P6 demo passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
