import { useEffect, useState } from "react";
import { fetchFailureCases } from "../api/client";
import type { FailureCasesStatus } from "../api/types";

export function FailureCases() {
  const [status, setStatus] = useState<FailureCasesStatus | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetchFailureCases()
      .then((body) => {
        if (!cancelled) setStatus(body);
      })
      .catch(() => {
        if (!cancelled) setError("故障案例暂时读不到。");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="cases-body">
      <p className="cases-kicker">只读 · 不能在这里改评测</p>
      {status ? (
        <>
          <p className="cases-note">{status.ci}</p>
          <p className="cases-note">{status.measured.model}</p>
          <ul className="cases-list">
            {status.cases.map((item) => (
              <li key={item.id}>
                <strong>{item.title}</strong>
                <span className="mono cases-signal">{item.signal}</span>
                <span>{item.saw}</span>
                <span className="cases-do">{item.do}</span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="cases-note">{error || "正在读取故障案例。"}</p>
      )}
    </div>
  );
}
