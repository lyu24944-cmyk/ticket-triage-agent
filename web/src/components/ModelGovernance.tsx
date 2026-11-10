import { useEffect, useState } from "react";
import { fetchModelGovernance } from "../api/client";
import type { ModelGovernanceStatus } from "../api/types";

export function ModelGovernance() {
  const [status, setStatus] = useState<ModelGovernanceStatus | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetchModelGovernance()
      .then((body) => {
        if (!cancelled) setStatus(body);
      })
      .catch(() => {
        if (!cancelled) setError("模型治理状态暂时读不到。");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const pinLabel =
    status == null
      ? ""
      : status.pin_status === "ok" && status.prompt_pin
        ? `${status.prompt_pin} · ${status.prompt_rev ?? ""} · ${status.prompt_version ?? ""}`
        : "销钉无法识别";

  return (
    <div className="gov-body">
      <p className="gov-kicker">只读 · 不能在这里切换销钉</p>
      {status ? (
        <>
          <p className="gov-pinline">
            <span className="gov-kicker">PROMPT_PIN</span>{" "}
            <span className="mono">{pinLabel}</span>
          </p>
          <p className="gov-note">
            {status.llm_provider === "other" ? "提供者未列入已知名单。" : `提供者 ${status.llm_provider}。`}
            {status.path_note}
          </p>
          <ul className="gov-signals">
            {status.signals.map((signal) => (
              <li key={signal.code}>
                <strong>{signal.label}</strong>
                <span className="mono gov-code">{signal.code}</span>
                <span>{signal.effect}</span>
              </li>
            ))}
          </ul>
          <p className="gov-note">{status.rollback}</p>
          <p className="gov-note">{status.ci}</p>
        </>
      ) : (
        <p className="gov-note">{error || "正在读取模型治理状态。"}</p>
      )}
    </div>
  );
}
