import { useState } from "react";
import {
  ApiError,
  AUTH_ERROR_ZH,
  createRun,
  waitForRun,
} from "../api/client";

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

  async function submit() {
    setError("");
    const key = serviceKey.trim();
    sessionStorage.setItem(SERVICE_KEY, key);
    if (!key) {
      setError("需要 SERVICE_API_KEY");
      return;
    }
    const ticket_id = ticketIdFromSubject(subject);
    onBusy(true, "起草中");
    try {
      const created = await createRun(key, {
        ticket_id,
        subject: subject.trim(),
        body: body.trim(),
      });
      const traceId = created.trace_id;
      if (!traceId) {
        setError("无 trace_id");
        return;
      }
      const done = await waitForRun(key, traceId);
      if (!done || done.status === "running") {
        setError("超时");
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
      onBusy(false);
    }
  }

  return (
    <div className="block">
      <h2>新建</h2>
      <div className="field">
        <input
          id="service-key"
          type="password"
          autoComplete="off"
          placeholder="SERVICE_API_KEY"
          aria-label="受理密钥"
          value={serviceKey}
          onChange={(e) => setServiceKey(e.target.value)}
        />
        <input
          id="subject"
          placeholder="主题"
          aria-label="主题"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
        />
        <textarea
          id="body"
          rows={3}
          placeholder="正文"
          aria-label="正文"
          value={body}
          onChange={(e) => setBody(e.target.value)}
        />
      </div>
      <div className="actions">
        <button type="button" className="primary" onClick={submit}>
          送审
        </button>
      </div>
      <p className="error">{error}</p>
    </div>
  );
}
