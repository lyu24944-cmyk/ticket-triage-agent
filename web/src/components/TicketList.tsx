import {
  categoryLabel,
  formatRelative,
  priorityLabel,
  statusLabel,
} from "../format";

export type QueueItem = {
  ticket_id: string;
  subject: string;
  category?: string | null;
  priority?: string | null;
  status?: string;
  updated_at?: string;
  warn?: boolean;
};

type Props = {
  kind: "pending" | "closed";
  items: QueueItem[];
  selectedId: string | null;
  nextCursor: string | null;
  phase: "loading" | "ready" | "error";
  refreshing: boolean;
  loadingMore: boolean;
  moreError: string;
  query: string;
  shortClosedQuery: boolean;
  onSelect: (ticketId: string) => void;
  onLoadMore: () => void;
  onRetry: () => void;
  onCreate: () => void;
};

export function TicketList({
  kind,
  items,
  selectedId,
  nextCursor,
  phase,
  refreshing,
  loadingMore,
  moreError,
  query,
  shortClosedQuery,
  onSelect,
  onLoadMore,
  onRetry,
  onCreate,
}: Props) {
  if (phase === "loading") {
    return (
      <div className="skel-list" aria-busy="true" aria-live="polite">
        <p className="sr-only">正在加载队列</p>
        {Array.from({ length: 5 }, (_, index) => (
          <div className="skel-row" key={index}>
            <div className="skel skel-dot" />
            <div>
              <div className="skel skel-line" />
              <div className="skel skel-line short" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (phase === "error") {
    return (
      <div className="empty-state" role="alert">
        <h3>队列没有加载成功</h3>
        <p>{moreError || "请检查人员令牌和网络后再试。"}</p>
        <button type="button" className="btn ghost" onClick={onRetry}>
          重试
        </button>
      </div>
    );
  }

  return (
    <div className={refreshing ? "queue-body is-refreshing" : "queue-body"} aria-busy={refreshing}>
      {items.length === 0 ? (
        <div className="empty-state">
          {query ? (
            <>
              <h3>没有匹配结果</h3>
              <p>
                {kind === "closed" && shortClosedQuery
                  ? `已加载的已处理工单里没有「${query}」。满 3 个字后会搜索全部已处理工单。`
                  : `没有主题、编号或正文包含「${query}」的${kind === "pending" ? "待审" : "已处理"}工单。`}
              </p>
            </>
          ) : kind === "pending" ? (
            <>
              <h3>待审队列是空的</h3>
              <p>没有等待审核的工单。可以新建一张，或等进线后再刷新。</p>
              <button type="button" className="btn ghost" onClick={onCreate}>
                新建工单
              </button>
            </>
          ) : (
            <>
              <h3>还没有已处理记录</h3>
              <p>通过后出现在已结案，驳回后出现在已升级。历史会保留。</p>
            </>
          )}
        </div>
      ) : (
        <ul className="list" aria-label={kind === "pending" ? "待审工单" : "已处理工单"}>
          {items.map((item) => {
            const active = item.ticket_id === selectedId;
            const level = kind === "pending" ? item.priority || "unknown" : item.status || "unknown";
            return (
              <li key={item.ticket_id}>
                <button
                  id={`ticket-${item.ticket_id}`}
                  type="button"
                  className="ticket-row"
                  aria-current={active ? "true" : undefined}
                  onClick={() => onSelect(item.ticket_id)}
                >
                  <span className="prio" data-level={level} aria-hidden />
                  <span className="ticket-main">
                    <span className="ticket-subject">{item.subject || "（无主题）"}</span>
                    <span className="ticket-meta">
                      {kind === "pending" ? (
                        <>
                          <span>{categoryLabel(item.category)}</span>
                          <span aria-hidden>·</span>
                          <span>{priorityLabel(item.priority)}</span>
                        </>
                      ) : (
                        <span className={`status-text status-${item.status || "unknown"}`}>
                          {statusLabel(item.status)}
                        </span>
                      )}
                      {item.warn ? <span className="tag-warn">模型回退</span> : null}
                    </span>
                  </span>
                  <span className="ticket-time">{formatRelative(item.updated_at)}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {nextCursor ? (
        <div className="load-more">
          <button type="button" className="btn ghost" disabled={loadingMore || refreshing} onClick={onLoadMore}>
            {loadingMore ? "加载中" : `继续加载（已显示 ${items.length}）`}
          </button>
          {moreError && phase === "ready" ? <p className="field-error">{moreError}</p> : null}
        </div>
      ) : items.length > 0 ? (
        <p className="list-end">已显示全部 {items.length} 条</p>
      ) : null}
    </div>
  );
}
