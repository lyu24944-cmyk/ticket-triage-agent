import { useState } from "react";
import { ApiError, AUTH_ERROR_ZH, createRun, waitForRun } from "../api/client";

type Props = {
  onCreated: (ticketId: string) => void;
  onBusy: (busy: boolean, label?: string) => void;
};

const SERVICE_KEY = "service_api_key";

function ticketIdFromSubject(subject: string): string {
  const stamp = Date.now().toString(36);
  const slug = subject
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 24);
  return `demo-${slug || "ticket"}-${stamp}`.slice(0, 64);
}

export function CreateTicket({ onCreated, onBusy }: Props) {
  const [serviceKey, setServiceKey] = useState(
    () => sessionStorage.getItem(SERVICE_KEY) || "",
  );
  const [subject, setSubject] = useState("紧急退款");
  const [body, setBody] = useState("我被重复扣款，需要退款");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    setError("");
    const key = serviceKey.trim();
    sessionStorage.setItem(SERVICE_KEY, key);
    if (!key) {
      setError("需要 SERVICE_API_KEY");
      return;
    }
    const ticket_id = ticketIdFromSubject(subject);
    setBusy(true);
    onBusy(true, "正在起草，完成后进入待审队列");
    try {
      const created = await createRun(key, {
        ticket_id,
        subject: subject.trim(),
        body: body.trim(),
      });
      const traceId = created.trace_id;
      if (!traceId) {
        setError("受理没有返回 trace_id");
        return;
      }
      const done = await waitForRun(key, traceId);
      if (!done || done.status === "running") {
        setError("起草超时，可稍后在待审队列里刷新");
        return;
      }
      if (done.errors?.length && done.status !== "awaiting_human") {
        setError(done.errors.join(" "));
      }
      onCreated(ticket_id);
    } catch (err) {
      const code = err instanceof ApiError ? err.code : "create_failed";
      setError(AUTH_ERROR_ZH[code] || (err instanceof Error ? err.message : "失败"));
    } finally {
      setBusy(false);
      onBusy(false);
    }
  }

  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <div className="composer-head">
        <h2>新建工单</h2>
        <p>用受理密钥送进分流。草稿生成后出现在待审队列。</p>
      </div>
      {error ? (
        <div className="alert alert-danger" role="alert">
          {error}
        </div>
      ) : null}
      <label className="field" htmlFor="service-key">
        <span>受理密钥</span>
        <input
          id="service-key"
          type="password"
          autoComplete="off"
          spellCheck={false}
          placeholder="SERVICE_API_KEY"
          aria-label="受理密钥"
          value={serviceKey}
          disabled={busy}
          onChange={(event) => setServiceKey(event.target.value)}
        />
      </label>
      <label className="field" htmlFor="subject">
        <span>主题</span>
        <input
          id="subject"
          placeholder="主题"
          aria-label="主题"
          value={subject}
          disabled={busy}
          onChange={(event) => setSubject(event.target.value)}
        />
      </label>
      <label className="field" htmlFor="body">
        <span>正文</span>
        <textarea
          id="body"
          rows={3}
          placeholder="正文"
          aria-label="正文"
          value={body}
          disabled={busy}
          onChange={(event) => setBody(event.target.value)}
        />
      </label>
      <button id="create" type="submit" className="btn primary block" disabled={busy}>
        {busy ? "起草中" : "送审"}
      </button>
    </form>
  );
}
