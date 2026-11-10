import { useEffect, useState, type ReactNode } from "react";
import { ApiError, authMessage, decideHuman, fetchTrace, reopenTicket } from "../api/client";
import type { AuthMode, ClosedItem, PendingItem, TraceSpan } from "../api/types";
import {
  NODE_LABEL,
  categoryLabel,
  formatAbsolute,
  formatRelative,
  priorityLabel,
  spanTone,
  statusLabel,
} from "../format";

type Props = {
  mode: AuthMode;
  token: string;
  kind: "pending" | "closed" | null;
  pending: PendingItem | null;
  closed: ClosedItem | null;
  selectedId: string | null;
  bootState: "idle" | "loading" | "ready" | "error";
  pendingCountLabel: string;
  closedCountLabel: string;
  onAuthError: (message: string) => void;
  onDecided: (ticketId: string, status: string) => void;
  onReopened: (ticketId: string) => void;
};

function modelWarnings(errors: string[] | undefined): string[] {
  return (errors || [])
    .filter(
      (error) =>
        error.startsWith("classify_model_failed") ||
        error.startsWith("draft_model_failed") ||
        error.startsWith("act_model_failed"),
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

function otherErrors(errors: string[] | undefined): string[] {
  return (errors || []).filter(
    (error) =>
      !error.startsWith("classify_model_failed") &&
      !error.startsWith("draft_model_failed") &&
      !error.startsWith("act_model_failed"),
  );
}

function loginHint(mode: AuthMode): string {
  return mode === "oidc" ? "需要 IdP 令牌" : "需要登录";
}

function Workspace({ children, dock }: { children: ReactNode; dock?: ReactNode }) {
  return (
    <div className="workspace">
      <div className="stage-scroll">{children}</div>
      {dock}
    </div>
  );
}

export function DetailPanel({
  mode,
  token,
  kind,
  pending,
  closed,
  selectedId,
  bootState,
  pendingCountLabel,
  closedCountLabel,
  onAuthError,
  onDecided,
  onReopened,
}: Props) {
  const [edits, setEdits] = useState("");
  const [actionError, setActionError] = useState("");
  const [spans, setSpans] = useState<TraceSpan[]>([]);
  const [traceState, setTraceState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [busy, setBusy] = useState<"approve" | "reject" | "reopen" | null>(null);
  const [confirmReopen, setConfirmReopen] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setEdits("");
    setActionError("");
    setConfirmReopen(false);
    setCopied(false);
    setSpans([]);
    if (!token || kind !== "pending" || !pending?.trace_id) {
      setTraceState("idle");
      return;
    }
    let cancelled = false;
    setTraceState("loading");
    fetchTrace(token, pending.trace_id)
      .then((trace) => {
        if (cancelled) return;
        setSpans(trace.spans || []);
        setTraceState("ready");
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) {
          onAuthError(authMessage(err.code, mode));
        }
        setTraceState("error");
      });
    return () => {
      cancelled = true;
    };
  }, [token, kind, pending?.ticket_id, pending?.trace_id, mode, onAuthError]);

  async function decide(approved: boolean) {
    if (!pending) return;
    if (!token) {
      setActionError(loginHint(mode));
      return;
    }
    setBusy(approved ? "approve" : "reject");
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
      setBusy(null);
    }
  }

  async function sendBack() {
    if (!closed) return;
    if (!confirmReopen) {
      setConfirmReopen(true);
      return;
    }
    if (!token) {
      setActionError(loginHint(mode));
      return;
    }
    setBusy("reopen");
    setActionError("");
    try {
      const result = await reopenTicket(token, closed.ticket_id);
      if (!result.awaiting_human) {
        setActionError((result.errors || ["重新送审失败"]).join(" "));
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
      setBusy(null);
    }
  }

  async function copyId(id: string) {
    try {
      await navigator.clipboard.writeText(id);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  }

  if (bootState === "loading" && !pending && !closed) {
    return (
      <Workspace>
      <div className="panel workspace-skel" aria-busy="true">
        <p className="sr-only">正在打开审核台</p>
        <div className="skel skel-line short" />
        <div className="skel skel-title" />
        <div className="detail-grid">
          <div className="skel skel-block" />
          <div className="skel skel-block" />
        </div>
      </div>
      </Workspace>
    );
  }

  if (kind === "pending" && pending) {
    const warnings = modelWarnings(pending.errors);
    const extras = otherErrors(pending.errors);
    const hits = pending.kb_hits || [];
    const hitIds = new Set(hits.map((hit) => hit.id).filter(Boolean));
    const extraCitations = (pending.citations || []).filter((id) => id && !hitIds.has(id));
    const calls = pending.tool_calls || [];
    const edited = edits.trim().length > 0;
    const maxSpan = Math.max(...spans.map((span) => Number(span.duration_ms) || 0), 1);
    const totalSpan = spans.reduce((sum, span) => sum + (Number(span.duration_ms) || 0), 0);

    const dock = (
      <form
        className="decision-dock"
        onSubmit={(event) => {
          event.preventDefault();
          void decide(true);
        }}
      >
        <div className="decision-inner">
          <label className="field" htmlFor="edits">
            <span>修改稿</span>
            <textarea
              id="edits"
              rows={2}
              placeholder="可留空。填写后，通过会以修改稿结案；驳回会把这段文字记入升级备注。"
              aria-label="修改稿"
              value={edits}
              onChange={(event) => setEdits(event.target.value)}
            />
          </label>
          <div className="decision-side">
            <div className="actions">
              <button id="approve" type="submit" className="btn primary" disabled={busy !== null}>
                {busy === "approve" ? "提交中" : edited ? "修改后通过" : "通过并结案"}
              </button>
              <button
                id="reject"
                type="button"
                className="btn danger"
                disabled={busy !== null}
                onClick={() => void decide(false)}
              >
                {busy === "reject" ? "提交中" : "驳回并升级"}
              </button>
            </div>
            <p className="hint">通过写入已结案，驳回写入已升级。修改稿不会单独再跑一遍模型。</p>
            {actionError ? (
              <div className="alert alert-danger" role="alert">
                {actionError}
              </div>
            ) : null}
          </div>
        </div>
      </form>
    );

    return (
      <Workspace
        dock={dock}
        children={
      <article className="panel" key={pending.ticket_id}>
        <header className="panel-head">
          <div className="kicker">
            <span className="mono">{pending.ticket_id}</span>
            <button type="button" className="btn ghost btn-tiny" onClick={() => void copyId(pending.ticket_id)}>
              {copied ? "已复制" : "复制编号"}
            </button>
            <span>{formatRelative(pending.updated_at) || "时间未知"}</span>
            <span className="kicker-abs">{formatAbsolute(pending.updated_at)}</span>
          </div>
          <h2>{pending.subject || "（无主题）"}</h2>
          <div className="chips">
            <span className="chip">
              {categoryLabel(pending.category)}
              {pending.category ? <span className="chip-code">{pending.category}</span> : null}
            </span>
            <span className="chip" data-level={pending.priority || "unknown"}>
              {priorityLabel(pending.priority)}
              {pending.priority ? <span className="chip-code">{pending.priority}</span> : null}
            </span>
            <span className="chip chip-live">待审</span>
            {pending.trace_id ? (
              <span className="chip" title={pending.trace_id}>
                <span className="chip-code">trace {pending.trace_id.slice(0, 8)}</span>
              </span>
            ) : null}
          </div>
        </header>

        {warnings.map((warning) => (
          <div className="alert alert-warn" role="status" key={warning}>
            {warning}
          </div>
        ))}
        {extras.length ? (
          <div className="alert alert-danger" role="status">
            <strong>运行记录了这些问题</strong>
            <ul className="error-codes">
              {extras.map((error) => (
                <li key={error}>{error}</li>
              ))}
            </ul>
          </div>
        ) : null}

        <div className="detail-grid">
          <section className="section">
            <h3>客户原文</h3>
            <div className="prose-card">{pending.body || "（无正文）"}</div>
          </section>
          <section className="section">
            <h3>回复草稿</h3>
            <pre className="draft">{pending.draft_body || "草稿是空的。"}</pre>
          </section>
        </div>

        <section className="section">
          <h3>知识库引用</h3>
          {hits.length === 0 && extraCitations.length === 0 ? (
            <p className="quiet">这次没有检索到可引用的政策。</p>
          ) : (
            <>
              {hits.map((hit) => (
                <article className="hit" key={String(hit.id || hit.title)}>
                  <header>
                    <strong>{hit.title || "未命名文档"}</strong>
                    {hit.id ? <span className="mono">{hit.id}</span> : null}
                  </header>
                  {hit.body ? <p>{hit.body}</p> : null}
                </article>
              ))}
              {extraCitations.length ? (
                <p className="quiet">
                  另有引用编号：<span className="mono">{extraCitations.join("、")}</span>
                </p>
              ) : null}
            </>
          )}
        </section>

        <section className="section">
          <h3>本次工具</h3>
          {calls.length === 0 ? (
            <p className="quiet">这次运行没有调用工具。</p>
          ) : (
            <table className="grid-table">
              <thead>
                <tr>
                  <th>工具</th>
                  <th>结果</th>
                </tr>
              </thead>
              <tbody>
                {calls.map((call, index) => {
                  const err = String(call.error || "失败");
                  const short = err.length > 80 ? `${err.slice(0, 48).replace(/\s+/g, " ")}…` : err;
                  return (
                    <tr key={`${call.name}-${index}`}>
                      <td className="mono">{call.name}</td>
                      <td className={call.ok ? "ok" : "bad"} title={call.ok ? undefined : err}>
                        {call.ok ? "成功" : short}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </section>

        <section className="section">
          <h3>
            节点耗时
            {traceState === "ready" && spans.length ? (
              <span className="section-aside">合计 {Math.round(totalSpan)} ms</span>
            ) : null}
          </h3>
          {traceState === "loading" ? (
            <div className="skel-list compact" aria-busy="true">
              <p className="sr-only">正在读取节点追踪</p>
              <div className="skel skel-line" />
              <div className="skel skel-line" />
              <div className="skel skel-line short" />
            </div>
          ) : null}
          {traceState === "error" ? <p className="quiet">节点追踪暂时不可用。</p> : null}
          {traceState === "ready" && spans.length === 0 ? <p className="quiet">还没有节点记录。</p> : null}
          {traceState === "ready" && spans.length > 0 ? (
            <div className="spans">
              {spans.map((span, index) => {
                const ms = Number(span.duration_ms) || 0;
                const tone = spanTone(span.status);
                return (
                  <div className="span-line" key={`${span.node}-${index}`}>
                    <div className="span-name">
                      <strong>{NODE_LABEL[span.node] || span.node}</strong>
                      <span className="mono">{span.node}</span>
                    </div>
                    <span className="bar" aria-hidden>
                      <i data-tone={tone} style={{ width: `${Math.max(4, (ms / maxSpan) * 100)}%` }} />
                    </span>
                    <span className="mono span-ms">{Math.round(ms)} ms</span>
                    <span className={tone === "bad" ? "bad" : "muted"}>{span.status}</span>
                  </div>
                );
              })}
            </div>
          ) : null}
        </section>

      </article>
        }
      />
    );
  }

  if (kind === "closed" && closed) {
    const history = closed.history || [];
    return (
      <Workspace
        dock={
          <div className="decision-dock">
            <div className="decision-inner decision-inner-closed">
              <p className="hint">重新送审会写回待审队列，并在历史上追加一条记录。已结案或已升级的状态会保留。</p>
              <div className="actions">
                <button id="reopen" type="button" className="btn primary" disabled={busy !== null} onClick={() => void sendBack()}>
                  {busy === "reopen" ? "提交中" : confirmReopen ? "确认重新送审" : "重新送审"}
                </button>
                {confirmReopen && busy === null ? (
                  <button type="button" className="btn ghost" onClick={() => setConfirmReopen(false)}>
                    取消
                  </button>
                ) : null}
              </div>
              {actionError ? (
                <div className="alert alert-danger" role="alert">
                  {actionError}
                </div>
              ) : null}
            </div>
          </div>
        }
      >
      <article className="panel" key={closed.ticket_id}>
        <header className="panel-head">
          <div className="kicker">
            <span className="mono">{closed.ticket_id}</span>
            <button type="button" className="btn ghost btn-tiny" onClick={() => void copyId(closed.ticket_id)}>
              {copied ? "已复制" : "复制编号"}
            </button>
            <span>{formatAbsolute(closed.updated_at)}</span>
          </div>
          <h2>{closed.subject || "（无主题）"}</h2>
          <div className="chips">
            <span className="chip" data-level={closed.status}>
              {statusLabel(closed.status)}
              <span className="chip-code">{closed.status}</span>
            </span>
          </div>
        </header>
        <section className="section">
          <h3>客户原文</h3>
          <div className="prose-card">{closed.body || "（无正文）"}</div>
        </section>
        <section className="section">
          <h3>状态历史</h3>
          {history.length === 0 ? (
            <p className="quiet">没有留下状态历史。</p>
          ) : (
            <ol className="timeline">
              {history.map((entry, index) => (
                <li key={`${entry.status}-${index}`}>
                  <span className="timeline-dot" data-level={entry.status || "unknown"} aria-hidden />
                  <div>
                    <strong>{statusLabel(entry.status)}</strong>
                    {entry.status ? <span className="mono"> {entry.status}</span> : null}
                    {entry.reviewer ? <span className="muted"> · {entry.reviewer}</span> : null}
                    {entry.note ? <p>{entry.note}</p> : null}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </section>
      </article>
      </Workspace>
    );
  }

  if (selectedId) {
    return (
      <Workspace>
      <div className="workspace-empty">
        <p className="eyebrow">当前筛选</p>
        <h2>这张工单不在结果里</h2>
        <p>编号 {selectedId} 不在当前队列页。清除搜索，或加载更多后再打开。</p>
      </div>
      </Workspace>
    );
  }

  if (bootState === "error") {
    return (
      <Workspace>
      <div className="workspace-empty">
        <p className="eyebrow">队列</p>
        <h2>暂时打不开工单</h2>
        <p>左侧保留了失败原因。网络或令牌恢复后点重试。</p>
      </div>
      </Workspace>
    );
  }

  return (
    <Workspace>
    <div className="workspace-empty">
      <p className="eyebrow">阅读区</p>
      <h2>选择一张工单</h2>
      <p>打开后可以看客户原文、回复草稿、引用、工具调用和每个节点的耗时。通过后结案，驳回则升级。</p>
      <dl className="stat-pair">
        <div>
          <dt>待审</dt>
          <dd>{pendingCountLabel}</dd>
        </div>
        <div>
          <dt>已处理</dt>
          <dd>{closedCountLabel}</dd>
        </div>
      </dl>
      <p className="hint">快捷键：J / K 或方向键移动，/ 聚焦搜索。</p>
    </div>
    </Workspace>
  );
}
