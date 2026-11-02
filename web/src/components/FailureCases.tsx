import { useEffect, useState } from "react";
import { fetchFailureCases } from "../api/client";
import type { FailureCasesStatus } from "../api/types";

type Props = {
  variant: "gate" | "desk" | "stage";
};

export function FailureCases({ variant }: Props) {
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

  const body = (
    <div className="cases-body">
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

  if (variant === "desk") {
    return (
      <details className="cases cases-desk">
        <summary>
          故障案例
          <span className="cases-summary-pin">只读 · 三次评测失败</span>
        </summary>
        <p className="cases-kicker">只读 · 不能在这里改评测</p>
        {body}
      </details>
    );
  }

  return (
    <section className={`cases cases-${variant}`} aria-label="故障案例">
      <h2>故障案例</h2>
      <p className="cases-kicker">只读 · 不能在这里改评测</p>
      {body}
    </section>
  );
}
