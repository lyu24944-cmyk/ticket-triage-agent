export type AuthMode = "mint" | "oidc";

export type AuthModeResponse = {
  mode: AuthMode;
  oidc_issuer: string | null;
  oidc_audience: string | null;
};

export type MintTokenResponse = {
  access_token: string;
  token_type: string;
  expires_at: number | null;
  sub: string;
  tenant_id: string;
};

export type ToolCall = {
  name: string;
  ok: boolean;
  error?: string | null;
};

export type KbHit = {
  id?: string;
  title?: string;
  body?: string;
};

export type PendingItem = {
  ticket_id: string;
  trace_id: string;
  updated_at?: string;
  subject: string;
  body: string;
  category?: string | null;
  priority?: string | null;
  draft_body: string;
  citations?: string[];
  kb_hits?: KbHit[];
  errors?: string[];
  tool_calls?: ToolCall[];
};

export type ClosedItem = {
  ticket_id: string;
  subject: string;
  body: string;
  status: string;
  updated_at?: string;
  history?: Array<{
    status?: string;
    note?: string | null;
    reviewer?: string | null;
  }>;
};

export type Page<T> = {
  items: T[];
  next_cursor: string | null;
};

export type TraceSpan = {
  node: string;
  status: string;
  duration_ms: number;
};

export type TraceResponse = {
  spans?: TraceSpan[];
};

export type RunResult = {
  ticket_id: string;
  status: string;
  awaiting_human?: boolean;
  trace_id?: string;
  errors?: string[];
  error?: { code: string; message: string };
};

export type ApiErrorBody = {
  error?: { code: string; message: string; details?: unknown };
  errors?: string[];
  trace_id?: string | null;
};
