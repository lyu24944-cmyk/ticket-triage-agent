import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  authMessage,
  fetchAuthMode,
  fetchClosed,
  fetchPending,
} from "./api/client";
import type { AuthMode, ClosedItem, PendingItem } from "./api/types";
import { AuthPanel } from "./components/AuthPanel";
import { CreateTicket } from "./components/CreateTicket";
import { DetailPanel } from "./components/DetailPanel";
import { TicketList } from "./components/TicketList";

const TOKEN_KEY = "review_access_token";
const PAGE_SIZE = 20;

const STATUS: Record<string, string> = {
  resolved: "已结案",
  escalated: "已升级",
  pending: "重新送审",
  open: "新建",
};

export default function App() {
  const [mode, setMode] = useState<AuthMode | null>(null);
  const [token, setToken] = useState(
    () => sessionStorage.getItem(TOKEN_KEY) || "",
  );
  const [sessionLabel, setSessionLabel] = useState("");
  const [authError, setAuthError] = useState("");

  const [pending, setPending] = useState<PendingItem[]>([]);
  const [pendingCursor, setPendingCursor] = useState<string | null>(null);
  const [closed, setClosed] = useState<ClosedItem[]>([]);
  const [closedCursor, setClosedCursor] = useState<string | null>(null);
  const [loadingMorePending, setLoadingMorePending] = useState(false);
  const [loadingMoreClosed, setLoadingMoreClosed] = useState(false);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedKind, setSelectedKind] = useState<"pending" | "closed" | null>(
    null,
  );
  const [stageMessage, setStageMessage] = useState<string | null>(null);
  const [doneBanner, setDoneBanner] = useState<string | null>(null);

  useEffect(() => {
    fetchAuthMode()
      .then((body) => setMode(body.mode === "oidc" ? "oidc" : "mint"))
      .catch(() => setMode("mint"));
  }, []);

  const handleAuthError = useCallback((message: string) => {
    setAuthError(message);
    setSessionLabel("");
  }, []);

  const loadLists = useCallback(
    async (bearer: string) => {
      if (!bearer) {
        setPending([]);
        setClosed([]);
        setPendingCursor(null);
        setClosedCursor(null);
        return;
      }
      try {
        const [p, c] = await Promise.all([
          fetchPending(bearer, { limit: PAGE_SIZE }),
          fetchClosed(bearer, { limit: PAGE_SIZE }),
        ]);
        setAuthError("");
        setPending(p.items);
        setPendingCursor(p.next_cursor);
        setClosed(c.items);
        setClosedCursor(c.next_cursor);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          handleAuthError(authMessage(err.code, mode || "mint"));
          setPending([]);
          setClosed([]);
          return;
        }
        handleAuthError(err instanceof Error ? err.message : "加载失败");
      }
    },
    [handleAuthError, mode],
  );

  useEffect(() => {
    if (token) void loadLists(token);
  }, [token, loadLists]);

  function onToken(next: string, label: string) {
    setToken(next);
    sessionStorage.setItem(TOKEN_KEY, next);
    setSessionLabel(label);
    setAuthError("");
    setDoneBanner(null);
  }

  async function loadMorePending() {
    if (!token || !pendingCursor) return;
    setLoadingMorePending(true);
    try {
      const page = await fetchPending(token, {
        cursor: pendingCursor,
        limit: PAGE_SIZE,
      });
      setPending((prev) => [...prev, ...page.items]);
      setPendingCursor(page.next_cursor);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        handleAuthError(authMessage(err.code, mode || "mint"));
      }
    } finally {
      setLoadingMorePending(false);
    }
  }

  async function loadMoreClosed() {
    if (!token || !closedCursor) return;
    setLoadingMoreClosed(true);
    try {
      const page = await fetchClosed(token, {
        cursor: closedCursor,
        limit: PAGE_SIZE,
      });
      setClosed((prev) => [...prev, ...page.items]);
      setClosedCursor(page.next_cursor);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        handleAuthError(authMessage(err.code, mode || "mint"));
      }
    } finally {
      setLoadingMoreClosed(false);
    }
  }

  const selectedPending = useMemo(
    () => pending.find((item) => item.ticket_id === selectedId) || null,
    [pending, selectedId],
  );
  const selectedClosed = useMemo(
    () => closed.find((item) => item.ticket_id === selectedId) || null,
    [closed, selectedId],
  );

  async function afterCreated(ticketId: string) {
    setStageMessage(null);
    await loadLists(token);
    setSelectedId(ticketId);
    setSelectedKind("pending");
    setDoneBanner(null);
  }

  return (
    <div className="app">
      <header className="brand">
        <div className="brand-mark">
          <h1>工单审核台</h1>
          <span className="brand-live">
            <i />
            live
          </span>
        </div>
      </header>
      <div className="layout">
        <aside className="rail">
          <AuthPanel
            mode={mode}
            token={token}
            sessionLabel={sessionLabel}
            onToken={onToken}
            onError={setAuthError}
            error={authError}
          />

          <TicketList
            title="待审"
            items={pending.map((item) => ({
              ticket_id: item.ticket_id,
              subject: item.subject,
              meta: `${item.category || "—"} / ${item.priority || "—"}`,
            }))}
            selectedId={selectedKind === "pending" ? selectedId : null}
            nextCursor={pendingCursor}
            loadingMore={loadingMorePending}
            onSelect={(id) => {
              setSelectedId(id);
              setSelectedKind("pending");
              setDoneBanner(null);
              setStageMessage(null);
            }}
            onLoadMore={loadMorePending}
          />

          <TicketList
            title="已处理"
            items={closed.map((item) => ({
              ticket_id: item.ticket_id,
              subject: item.subject,
              meta: STATUS[item.status] || item.status,
            }))}
            selectedId={selectedKind === "closed" ? selectedId : null}
            nextCursor={closedCursor}
            loadingMore={loadingMoreClosed}
            onSelect={(id) => {
              setSelectedId(id);
              setSelectedKind("closed");
              setDoneBanner(null);
              setStageMessage(null);
            }}
            onLoadMore={loadMoreClosed}
          />

          <CreateTicket
            onCreated={afterCreated}
            onBusy={(busy, label) => setStageMessage(busy ? label || "…" : null)}
          />
        </aside>

        <main className="stage">
          {doneBanner ? <div className="done-toast">{doneBanner}</div> : null}
          <DetailPanel
            mode={mode || "mint"}
            token={token}
            kind={selectedKind}
            pending={selectedPending}
            closed={selectedClosed}
            stageMessage={stageMessage}
            onAuthError={handleAuthError}
            onDecided={async (ticketId, status) => {
              setDoneBanner(`${ticketId} → ${STATUS[status] || status}`);
              setSelectedId(null);
              setSelectedKind(null);
              await loadLists(token);
            }}
            onReopened={async (ticketId) => {
              await loadLists(token);
              setSelectedId(ticketId);
              setSelectedKind("pending");
              setDoneBanner(null);
            }}
          />
        </main>
      </div>
    </div>
  );
}
