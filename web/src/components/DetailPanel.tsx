import { useEffect, useState } from "react";
import {
  ApiError,
  authMessage,
  decideHuman,
  fetchTrace,
  reopenTicket,
} from "../api/client";
import type { AuthMode, ClosedItem, PendingItem, TraceSpan } from "../api/types";

const STATUS: Record<string, string> = {
  resolved: "已结案",
  escalated: "已升级",
  pending: "重新送审",
  open: "新建",
  awaiting_human: "待审",
};

type Props = {
  mode: AuthMode;
  token: string;
  kind: "pending" | "closed" | null;
  pending: PendingItem | null;
  closed: ClosedItem | null;
  stageMessage: string | null;
  onAuthError: (message: string) => void;
  onDecided: (ticketId: string, status: string) => void;
  onReopened: (ticketId: string) => void;
};

function modelWarnings(errors: string[] | undefined): string[] {
  return (errors || [])
    .filter(
      (e) =>
        e.startsWith("classify_model_failed") ||
        e.startsWith("draft_model_failed") ||
        e.startsWith("act_model_failed"),
    )
    .map((error) => {
      if (error.startsWith("classify_model_failed")) {
        return "分类模型失败，当前分类由关键词规则生成。";
      }
      if (error.startsWith("act_model_failed")) {
        return "工具调度模型失败，已改用固定规则选择工具。";
      }
      return "草稿模型失败，当前草稿由 stub 生成。";
    });
}

export function DetailPanel({
  mode,
  token,
  kind,
  pending,
  closed,
  stageMessage,
  onAuthError,
  onDecided,
  onReopened,
}: Props) {
  const [edits, setEdits] = useState("");
  const [actionError, setActionError] = useState("");
  const [spans, setSpans] = useState<TraceSpan[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setEdits("");
    setActionError("");
    setSpans([]);
    if (!token || kind !== "pending" || !pending?.trace_id) return;
    let cancelled = false;
    fetchTrace(token, pending.trace_id)
      .then((trace) => {
        if (!cancelled) setSpans(trace.spans || []);
      })
      .catch(() => {
        if (!cancelled) setSpans([]);
      });
    return () => {
      cancelled = true;
    };
  }, [token, kind, pending?.ticket_id, pending?.trace_id]);

  async function decide(approved: boolean) {
    if (!pending) return;
    if (!token) {
      setActionError(mode === "oidc" ? "需要 IdP 令牌" : "需要登录");
      return;
    }
    setBusy(true);
    setActionError("");
    try {
      const result = await decideHuman(token, {
        ticket_id: pending.ticket_id,
        approved,
        edits: edits.trim() || null,
      });
      onDecided(pending.ticket_id, result.status);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onAuthError(authMessage(err.code, mode));
        return;
      }
      setActionError(err instanceof Error ? err.message : "失败");
    } finally {
      setBusy(false);
    }
  }

  async function sendBack() {
    if (!closed) return;
    if (!token) {
      setActionError(mode === "oidc" ? "需要 IdP 令牌" : "需要登录");
      return;
    }
    setBusy(true);
    setActionError("");
    try {
      const result = await reopenTicket(token, closed.ticket_id);
      if (!result.awaiting_human) {
        setActionError((result.errors || ["失败"]).join(" "));
        return;
      }
      onReopened(closed.ticket_id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onAuthError(authMessage(err.code, mode));
        return;
      }
      setActionError(err instanceof Error ? err.message : "失败");
    } finally {
      setBusy(false);
    }
  }

  if (stageMessage) {
    return (
      <div className="panel busy-panel" key="busy">
        <div className="spinner" aria-hidden />
        <div className="section-label">{stageMessage}</div>
      </div>
    );
  }

  if (kind === "pending" && pending) {
    const warnings = modelWarnings(pending.errors);
    return (
      <div className="panel" key={pending.ticket_id}>
        <div className="panel-head">
          <h3>{pending.subject}</h3>
          <div className="badge-row">
            <span className="badge">{pending.ticket_id}</span>
            <span className="badge">{pending.category || "—"}</span>
            <span className="badge">{pending.priority || "—"}</span>
          </div>
        </div>
        {warnings.length ? (
          <p className="warning">
            {warnings.map((w) => (
              <span key={w}>
                {w}
                <br />
              </span>
            ))}
          </p>
        ) : null}
        <p className="body-copy">{pending.body}</p>

        <section className="section">
          <h4 className="section-label">工具</h4>
          <ul className="tool-list">
            {(pending.tool_calls || []).length === 0 ? (
              <li className="empty">—</li>
            ) : (
              (pending.tool_calls || []).map((call, idx) => {
                const err = String(call.error || "fail");
                const short =
                  err.length > 80
                    ? `${err.slice(0, 48).replace(/\s+/g, " ")}…`
                    : err;
                return (
                  <li key={`${call.name}-${idx}`} title={call.ok ? undefined : err}>
                    <span>{call.name}</span>
                    <span className={call.ok ? "ok" : "bad"}>
                      {call.ok ? "ok" : short}
                    </span>
                  </li>
                );
              })
            )}
          </ul>
        </section>

        <section className="section">
          <h4 className="section-label">草稿</h4>
          <pre className="draft">{pending.draft_body}</pre>
        </section>

        <section className="section">
          <h4 className="section-label">引用</h4>
          {(pending.kb_hits || []).length === 0 ? (
            <p className="empty">—</p>
          ) : (
            (pending.kb_hits || []).map((hit) => (
              <div className="hit" key={String(hit.id || hit.title)}>
                <strong>{hit.title}</strong>
                <span className="meta">{hit.id}</span>
                <div>{hit.body}</div>
              </div>
            ))
          )}
        </section>

        <section className="decide">
          <textarea
            id="edits"
            rows={4}
            placeholder="改稿（可空）"
            aria-label="修改稿"
            value={edits}
            onChange={(e) => setEdits(e.target.value)}
          />
          <div className="actions">
            <button
              type="button"
              className="primary"
              disabled={busy}
              onClick={() => decide(true)}
            >
              通过
            </button>
            <button
              type="button"
              className="danger"
              disabled={busy}
              onClick={() => decide(false)}
            >
              驳回
            </button>
          </div>
          <p className="error">{actionError}</p>
        </section>

        {spans.length ? (
          <section className="section">
            <h4 className="section-label">节点</h4>
            <pre className="trace">
              {spans
                .map(
                  (span) =>
                    `${span.node}  ${span.status}  ${Number(span.duration_ms).toFixed(0)}ms`,
                )
                .join("\n")}
            </pre>
          </section>
        ) : null}
      </div>
    );
  }

  if (kind === "closed" && closed) {
    const history = (closed.history || [])
      .map((entry) => {
        const who = entry.reviewer ? `  ${entry.reviewer}` : "";
        const note = entry.note ? `  ${entry.note}` : "";
        return `${STATUS[entry.status || ""] || entry.status}${who}${note}`;
      })
      .join("\n");
    return (
      <div className="panel" key={closed.ticket_id}>
        <div className="panel-head">
          <h3>{closed.subject}</h3>
          <div className="badge-row">
            <span className="badge">{closed.ticket_id}</span>
            <span className="badge">{STATUS[closed.status] || closed.status}</span>
          </div>
        </div>
        <p className="body-copy">{closed.body}</p>
        <section className="section">
          <h4 className="section-label">历史</h4>
          <pre className="trace">{history || "—"}</pre>
        </section>
        <div className="actions">
          <button type="button" className="primary" disabled={busy} onClick={sendBack}>
            重新送审
          </button>
        </div>
        <p className="error">{actionError}</p>
      </div>
    );
  }

  return (
    <div className="panel panel-empty" key="empty">
      <div className="glyph" aria-hidden>
        —
      </div>
      <div className="hint">选一张工单</div>
    </div>
  );
}
