import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, authMessage, fetchAuthMode, fetchClosed, fetchPending } from "./api/client";
import type { AuthMode, ClosedItem, PendingItem } from "./api/types";
import { AuthPanel } from "./components/AuthPanel";
import { CreateTicket } from "./components/CreateTicket";
import { DetailPanel } from "./components/DetailPanel";
import { TicketList } from "./components/TicketList";
import {
  avatarText,
  closedQueryOnServer,
  countLabel,
  formatExpiry,
  readJwtClaims,
  statusLabel,
} from "./format";
import { IconRefresh, IconSearch } from "./icons";

const TOKEN_KEY = "review_access_token";
const PAGE_SIZE = 20;

type ListsStatus = "idle" | "loading" | "ready" | "error";
type QueueTab = "pending" | "closed";

function storedToken(): string {
  return sessionStorage.getItem(TOKEN_KEY) || "";
}

function labelFor(token: string): string {
  if (!token) return "";
  return readJwtClaims(token).sub || "已登录";
}

function isTypingTarget(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLElement &&
    (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)
  );
}

export default function App() {
  const [mode, setMode] = useState<AuthMode | null>(null);
  const [token, setToken] = useState(storedToken);
  const [sessionLabel, setSessionLabel] = useState(() => labelFor(storedToken()));
  const [authError, setAuthError] = useState("");
  const [now, setNow] = useState(() => Date.now());

  const [pending, setPending] = useState<PendingItem[]>([]);
  const [pendingCursor, setPendingCursor] = useState<string | null>(null);
  const [closed, setClosed] = useState<ClosedItem[]>([]);
  const [closedCursor, setClosedCursor] = useState<string | null>(null);
  const [listsStatus, setListsStatus] = useState<ListsStatus>(() => (storedToken() ? "loading" : "idle"));
  const [refreshing, setRefreshing] = useState(false);
  const [queueError, setQueueError] = useState("");
  const [loadingMorePending, setLoadingMorePending] = useState(false);
  const [loadingMoreClosed, setLoadingMoreClosed] = useState(false);
  const [moreError, setMoreError] = useState("");

  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [tab, setTab] = useState<QueueTab>("pending");
  const [composerOpen, setComposerOpen] = useState(false);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedKind, setSelectedKind] = useState<QueueTab | null>(null);
  const [stageMessage, setStageMessage] = useState<string | null>(null);
  const [doneBanner, setDoneBanner] = useState<string | null>(null);

  const searchRef = useRef<HTMLInputElement>(null);
  const requestSeq = useRef(0);
  const loadedOnce = useRef(false);

  const claims = useMemo(() => (token ? readJwtClaims(token) : {}), [token]);

  useEffect(() => {
    fetchAuthMode()
      .then((body) => setMode(body.mode === "oidc" ? "oidc" : "mint"))
      .catch(() => setMode("mint"));
  }, []);

  useEffect(() => {
    if (!token) return;
    const timer = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(timer);
  }, [token]);

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setDebouncedQuery("");
      return;
    }
    const handle = window.setTimeout(() => setDebouncedQuery(trimmed), 280);
    return () => window.clearTimeout(handle);
  }, [query]);

  const clearSession = useCallback((message = "") => {
    requestSeq.current += 1;
    sessionStorage.removeItem(TOKEN_KEY);
    loadedOnce.current = false;
    setToken("");
    setSessionLabel("");
    setAuthError(message);
    setPending([]);
    setClosed([]);
    setPendingCursor(null);
    setClosedCursor(null);
    setListsStatus("idle");
    setRefreshing(false);
    setQueueError("");
    setMoreError("");
    setSelectedId(null);
    setSelectedKind(null);
    setDoneBanner(null);
    setStageMessage(null);
  }, []);

  const loadLists = useCallback(
    async (bearer: string, q: string) => {
      const seq = ++requestSeq.current;
      if (!loadedOnce.current) setListsStatus("loading");
      else setRefreshing(true);
      setQueueError("");
      setMoreError("");
      const closedQ = closedQueryOnServer(q) ? q : undefined;
      try {
        const [nextPending, nextClosed, closedHead] = await Promise.all([
          fetchPending(bearer, { limit: PAGE_SIZE, q: q || undefined }),
          fetchClosed(bearer, { limit: PAGE_SIZE, q: closedQ }),
          closedQ ? fetchClosed(bearer, { limit: PAGE_SIZE }) : Promise.resolve(null),
        ]);
        if (seq !== requestSeq.current) return;
        let closedItems = nextClosed.items;
        if (closedHead && q) {
          const needle = q.toLowerCase();
          const seen = new Set(closedItems.map((item) => item.ticket_id));
          const extra = closedHead.items.filter((item) => {
            if (seen.has(item.ticket_id)) return false;
            return `${item.ticket_id} ${item.subject} ${item.body}`.toLowerCase().includes(needle);
          });
          closedItems = [...extra, ...closedItems];
        }
        setPending(nextPending.items);
        setPendingCursor(nextPending.next_cursor);
        setClosed(closedItems);
        setClosedCursor(nextClosed.next_cursor);
        setListsStatus("ready");
        loadedOnce.current = true;
      } catch (err) {
        if (seq !== requestSeq.current) return;
        if (err instanceof ApiError && err.status === 401) {
          clearSession(authMessage(err.code, mode || "mint"));
          return;
        }
        const message = err instanceof Error ? err.message : "加载失败";
        setQueueError(message);
        if (!loadedOnce.current) setListsStatus("error");
      } finally {
        if (seq === requestSeq.current) setRefreshing(false);
      }
    },
    [clearSession, mode],
  );

  useEffect(() => {
    if (!token) return;
    void loadLists(token, debouncedQuery);
  }, [token, debouncedQuery, loadLists]);

  const shownClosed = useMemo(() => {
    if (!debouncedQuery || closedQueryOnServer(debouncedQuery)) return closed;
    const needle = debouncedQuery.toLowerCase();
    return closed.filter((item) =>
      `${item.ticket_id} ${item.subject} ${item.body}`.toLowerCase().includes(needle),
    );
  }, [closed, debouncedQuery]);

  const activeItems = tab === "pending" ? pending : shownClosed;

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (!token || event.metaKey || event.ctrlKey || event.altKey) return;
      const typing = isTypingTarget(event.target);
      if (event.key === "/" && !typing) {
        event.preventDefault();
        searchRef.current?.focus();
        searchRef.current?.select();
        return;
      }
      if (event.key === "Escape") {
        if (typing && event.target instanceof HTMLElement) {
          event.target.blur();
          return;
        }
        if (query) {
          setQuery("");
          return;
        }
        if (composerOpen) setComposerOpen(false);
        return;
      }
      if (typing) return;
      if (event.key !== "j" && event.key !== "k" && event.key !== "ArrowDown" && event.key !== "ArrowUp") {
        return;
      }
      if (!activeItems.length) return;
      event.preventDefault();
      const ids = activeItems.map((item) => item.ticket_id);
      const current = selectedKind === tab ? selectedId : null;
      const index = current ? ids.indexOf(current) : -1;
      const delta = event.key === "j" || event.key === "ArrowDown" ? 1 : -1;
      const nextIndex =
        index === -1 ? (delta > 0 ? 0 : ids.length - 1) : (index + delta + ids.length) % ids.length;
      const id = ids[nextIndex];
      setSelectedId(id);
      setSelectedKind(tab);
      setDoneBanner(null);
      document.getElementById(`ticket-${id}`)?.scrollIntoView({ block: "nearest" });
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [token, query, composerOpen, activeItems, selectedId, selectedKind, tab]);

  function onToken(next: string, label: string) {
    sessionStorage.setItem(TOKEN_KEY, next);
    loadedOnce.current = false;
    setListsStatus("loading");
    setToken(next);
    setSessionLabel(label || labelFor(next));
    setAuthError("");
    setDoneBanner(null);
    setNow(Date.now());
  }

  function signOut() {
    clearSession("");
  }

  async function loadMorePending() {
    if (!token || !pendingCursor || loadingMorePending) return;
    setLoadingMorePending(true);
    setMoreError("");
    try {
      const page = await fetchPending(token, {
        cursor: pendingCursor,
        limit: PAGE_SIZE,
        q: debouncedQuery || undefined,
      });
      setPending((prev) => [...prev, ...page.items]);
      setPendingCursor(page.next_cursor);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession(authMessage(err.code, mode || "mint"));
        return;
      }
      setMoreError(err instanceof Error ? err.message : "加载更多失败");
    } finally {
      setLoadingMorePending(false);
    }
  }

  async function loadMoreClosed() {
    if (!token || !closedCursor || loadingMoreClosed) return;
    setLoadingMoreClosed(true);
    setMoreError("");
    try {
      const page = await fetchClosed(token, {
        cursor: closedCursor,
        limit: PAGE_SIZE,
        q: closedQueryOnServer(debouncedQuery) ? debouncedQuery : undefined,
      });
      setClosed((prev) => [...prev, ...page.items]);
      setClosedCursor(page.next_cursor);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession(authMessage(err.code, mode || "mint"));
        return;
      }
      setMoreError(err instanceof Error ? err.message : "加载更多失败");
    } finally {
      setLoadingMoreClosed(false);
    }
  }

  const selectedPending = useMemo(
    () => (selectedKind === "pending" ? pending.find((item) => item.ticket_id === selectedId) || null : null),
    [pending, selectedId, selectedKind],
  );
  const selectedClosed = useMemo(
    () => (selectedKind === "closed" ? closed.find((item) => item.ticket_id === selectedId) || null : null),
    [closed, selectedId, selectedKind],
  );

  async function afterCreated(ticketId: string) {
    setStageMessage(null);
    setComposerOpen(false);
    setTab("pending");
    setQuery("");
    setDebouncedQuery("");
    await loadLists(token, "");
    setSelectedId(ticketId);
    setSelectedKind("pending");
    setDoneBanner(`${ticketId} 已进入待审`);
  }

  const pendingCount = countLabel(pending.length, Boolean(pendingCursor), listsStatus === "loading");
  const closedCount = countLabel(
    debouncedQuery && !closedQueryOnServer(debouncedQuery) ? shownClosed.length : closed.length,
    Boolean(closedCursor) && !(debouncedQuery && !closedQueryOnServer(debouncedQuery)),
    listsStatus === "loading",
  );
  const shortClosedQuery = Boolean(debouncedQuery) && !closedQueryOnServer(debouncedQuery);
  const visibleItems = tab === "pending" ? pending : shownClosed;
  const linkLabel =
    listsStatus === "error" || queueError
      ? "同步失败"
      : listsStatus === "loading" || refreshing
        ? "同步中"
        : "队列已同步";

  if (!token) {
    return (
      <div className="gate">
        <section className="gate-copy">
          <div className="mark" aria-hidden>
            审
          </div>
          <h1>工单审核台</h1>
          <p>模型起草回复。你通过后才结案，驳回则升级。</p>
          <ul className="principles">
            <li>待审里看草稿、引用、工具调用和节点耗时</li>
            <li>修改稿会替换结案内容；驳回时记入升级备注</li>
            <li>已处理的单可以重新送审，原状态留在历史里</li>
          </ul>
        </section>
        <div className="gate-side">
          <AuthPanel mode={mode} onToken={onToken} onError={setAuthError} error={authError} />
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="mark" aria-hidden>
            审
          </div>
          <div>
            <h1>工单审核台</h1>
            <p>人审队列</p>
          </div>
        </div>
        <div className="top-stats" aria-live="polite">
          <span className={`link-state link-${queueError || listsStatus === "error" ? "error" : listsStatus}`}>
            <i />
            {linkLabel}
          </span>
          <span>
            <b>{pendingCount}</b> 待审
          </span>
          <span>
            <b>{closedCount}</b> 已处理
          </span>
        </div>
        <div className="top-session">
          <span className="avatar" aria-hidden>
            {avatarText(sessionLabel)}
          </span>
          <span className="who">
            <strong>{sessionLabel}</strong>
            <small>
              {mode === "oidc" ? "IdP 令牌" : "本地令牌"} · {formatExpiry(claims.exp, now)}
            </small>
          </span>
          <button type="button" className="bar-btn" onClick={signOut}>
            退出
          </button>
        </div>
      </header>

      <div className="layout">
        <aside className="rail">
          <div className="queue-head">
            <div className="tabs" role="tablist" aria-label="队列">
              <button
                type="button"
                className="tab"
                role="tab"
                aria-selected={tab === "pending"}
                onClick={() => setTab("pending")}
              >
                待审 <span className="count">{pendingCount}</span>
              </button>
              <button
                type="button"
                className="tab"
                role="tab"
                aria-selected={tab === "closed"}
                onClick={() => setTab("closed")}
              >
                已处理 <span className="count">{closedCount}</span>
              </button>
            </div>
            <div className="queue-tools">
              <label className="search">
                <span className="sr-only">搜索工单</span>
                <IconSearch />
                <input
                  ref={searchRef}
                  value={query}
                  placeholder="搜索编号、主题或正文"
                  aria-label="搜索工单"
                  onChange={(event) => setQuery(event.target.value)}
                />
              </label>
              <button
                type="button"
                className="icon-btn"
                aria-label="刷新队列"
                aria-busy={refreshing || listsStatus === "loading"}
                onClick={() => void loadLists(token, debouncedQuery)}
              >
                <IconRefresh />
              </button>
            </div>
            {tab === "closed" && debouncedQuery ? (
              <p className="search-note">
                {shortClosedQuery
                  ? `不足 3 个字时，已处理只过滤已加载的 ${closed.length} 条。`
                  : "主题和正文会搜索全部已处理工单。编号只额外匹配已加载的第一页。"}
              </p>
            ) : null}
            {queueError && listsStatus === "ready" ? (
              <div className="alert alert-danger" role="alert">
                <p>刷新失败：{queueError}</p>
                <button type="button" className="btn ghost btn-tiny" onClick={() => void loadLists(token, debouncedQuery)}>
                  重试
                </button>
              </div>
            ) : null}
          </div>

          {composerOpen ? (
            <CreateTicket
              onCreated={(ticketId) => void afterCreated(ticketId)}
              onBusy={(busy, label) => setStageMessage(busy ? label || "正在起草" : null)}
            />
          ) : null}

          <div className="queue-scroll">
            <TicketList
              kind={tab}
              items={visibleItems.map((item) => ({
                ticket_id: item.ticket_id,
                subject: item.subject,
                category: "category" in item ? item.category : undefined,
                priority: "priority" in item ? item.priority : undefined,
                status: "status" in item ? item.status : undefined,
                updated_at: item.updated_at,
                warn:
                  "errors" in item &&
                  (item.errors || []).some(
                    (error) =>
                      error.startsWith("classify_model_failed") ||
                      error.startsWith("act_model_failed") ||
                      error.startsWith("draft_model_failed"),
                  ),
              }))}
              selectedId={selectedKind === tab ? selectedId : null}
              nextCursor={tab === "pending" ? pendingCursor : closedCursor}
              phase={listsStatus === "idle" ? "loading" : listsStatus}
              refreshing={refreshing}
              loadingMore={tab === "pending" ? loadingMorePending : loadingMoreClosed}
              moreError={listsStatus === "error" ? queueError : moreError}
              query={debouncedQuery}
              shortClosedQuery={tab === "closed" && shortClosedQuery}
              onSelect={(id) => {
                setSelectedId(id);
                setSelectedKind(tab);
                setDoneBanner(null);
              }}
              onLoadMore={() => void (tab === "pending" ? loadMorePending() : loadMoreClosed())}
              onRetry={() => void loadLists(token, debouncedQuery)}
              onCreate={() => setComposerOpen(true)}
            />
          </div>

          <div className="rail-foot">
            <button type="button" className="btn ghost block" onClick={() => setComposerOpen((open) => !open)}>
              {composerOpen ? "收起新建" : "新建工单"}
            </button>
            <p className="hint">J / K 移动 · / 搜索</p>
          </div>
        </aside>

        <main className="stage">
          {stageMessage ? (
            <div className="banner" role="status">
              <span className="spinner" aria-hidden />
              {stageMessage}
            </div>
          ) : null}
          {doneBanner ? (
            <div className="banner banner-ok" role="status">
              <span>{doneBanner}</span>
              <button type="button" className="btn ghost btn-tiny" onClick={() => setDoneBanner(null)}>
                关闭
              </button>
            </div>
          ) : null}
          <DetailPanel
            mode={mode || "mint"}
            token={token}
            kind={selectedKind}
            pending={selectedPending}
            closed={selectedClosed}
            selectedId={selectedId}
            bootState={listsStatus}
            pendingCountLabel={pendingCount}
            closedCountLabel={closedCount}
            onAuthError={clearSession}
            onDecided={async (ticketId, status) => {
              setDoneBanner(`${ticketId} → ${statusLabel(status)}`);
              setSelectedId(null);
              setSelectedKind(null);
              setTab("closed");
              setQuery("");
              setDebouncedQuery("");
              await loadLists(token, "");
            }}
            onReopened={async (ticketId) => {
              setQuery("");
              setDebouncedQuery("");
              await loadLists(token, "");
              setTab("pending");
              setSelectedId(ticketId);
              setSelectedKind("pending");
              setDoneBanner(`${ticketId} 已重新送审`);
            }}
          />
        </main>
      </div>
    </div>
  );
}
