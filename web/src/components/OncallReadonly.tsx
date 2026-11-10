import { useEffect, useState } from "react";
import { fetchQueueDepth } from "../api/client";
import type { QueueDepth } from "../api/types";
import { FailureCases } from "./FailureCases";
import { ModelGovernance } from "./ModelGovernance";

function QueueDepthPanel() {
  const [queue, setQueue] = useState<QueueDepth | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetchQueueDepth()
      .then((body) => {
        if (!cancelled) setQueue(body);
      })
      .catch(() => {
        if (!cancelled) setError("队列深度暂时读不到。");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section className="oncall-section" aria-label="队列深度">
      <h2>队列</h2>
      {queue ? (
        <p className="oncall-note">
          <span className="mono">
            {queue.active} / {queue.limit}
          </span>
          <span>已受理未结束 / 上限。满了会拒绝新单，不要再起一个写进程。</span>
        </p>
      ) : (
        <p className="oncall-note">{error || "正在读取队列深度。"}</p>
      )}
    </section>
  );
}

export function OncallReadonly() {
  const [open, setOpen] = useState(false);

  return (
    <details
      className="oncall oncall-desk"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        值班只读
        <span className="oncall-summary">队列、销钉、故障案例</span>
      </summary>
      {open ? (
        <div className="oncall-body">
          <QueueDepthPanel />
          <section className="oncall-section" aria-label="模型治理">
            <h2>模型治理</h2>
            <ModelGovernance />
          </section>
          <section className="oncall-section" aria-label="故障案例">
            <h2>故障案例</h2>
            <FailureCases />
          </section>
        </div>
      ) : null}
    </details>
  );
}
