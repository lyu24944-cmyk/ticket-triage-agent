import { useState } from "react";
import { FailureCases } from "./FailureCases";
import { ModelGovernance } from "./ModelGovernance";

export function OncallReadonly() {
  const [open, setOpen] = useState(false);

  return (
    <details
      className="oncall oncall-desk"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        值班只读
        <span className="oncall-summary">销钉、故障案例</span>
      </summary>
      {open ? (
        <div className="oncall-body">
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
