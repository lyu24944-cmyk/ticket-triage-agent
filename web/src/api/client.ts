import type {
  ApiErrorBody,
  AuthModeResponse,
  ClosedItem,
  MintTokenResponse,
  Page,
  PendingItem,
  RunResult,
  TraceResponse,
} from "./types";

export class ApiError extends Error {
  code: string;
  status: number;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function codeFromBody(body: ApiErrorBody, fallback: string): string {
  if (body.error?.code) return body.error.code;
  if (body.errors?.[0]) return body.errors[0];
  return fallback;
}

function messageFromBody(body: ApiErrorBody, code: string): string {
  if (body.error?.message) return body.error.message;
  return code;
}

async function parseJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(path, init);
  const body = (await parseJson(response)) as ApiErrorBody & T;
  if (!response.ok) {
    const code = codeFromBody(body, "request_failed");
    throw new ApiError(response.status, code, messageFromBody(body, code));
  }
  return body as T;
}

function authHeaders(token: string, extra?: HeadersInit): HeadersInit {
  return {
    ...(extra || {}),
    Authorization: `Bearer ${token}`,
  };
}

export function fetchAuthMode(): Promise<AuthModeResponse> {
  return request<AuthModeResponse>("/v1/auth/review/mode");
}

export function mintToken(
  reviewKey: string,
  sub: string,
): Promise<MintTokenResponse> {
  return request<MintTokenResponse>("/v1/auth/review/token", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": reviewKey,
    },
    body: JSON.stringify({ sub }),
  });
}

export function fetchPending(
  token: string,
  opts: { cursor?: string | null; limit?: number } = {},
): Promise<Page<PendingItem>> {
  const params = new URLSearchParams();
  params.set("limit", String(opts.limit ?? 20));
  if (opts.cursor) params.set("cursor", opts.cursor);
  return request<Page<PendingItem>>(`/v1/runs/pending?${params}`, {
    headers: authHeaders(token),
  });
}

export function fetchClosed(
  token: string,
  opts: { cursor?: string | null; limit?: number } = {},
): Promise<Page<ClosedItem>> {
  const params = new URLSearchParams();
  params.set("limit", String(opts.limit ?? 20));
  if (opts.cursor) params.set("cursor", opts.cursor);
  return request<Page<ClosedItem>>(`/v1/tickets?${params}`, {
    headers: authHeaders(token),
  });
}

export function fetchTrace(token: string, traceId: string): Promise<TraceResponse> {
  return request<TraceResponse>(`/v1/traces/${encodeURIComponent(traceId)}`, {
    headers: authHeaders(token),
  });
}

export function decideHuman(
  token: string,
  payload: { ticket_id: string; approved: boolean; edits?: string | null },
): Promise<RunResult> {
  return request<RunResult>("/v1/runs/human", {
    method: "POST",
    headers: authHeaders(token, { "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
}

export function reopenTicket(token: string, ticketId: string): Promise<RunResult> {
  return request<RunResult>(`/v1/tickets/${encodeURIComponent(ticketId)}/reopen`, {
    method: "POST",
    headers: authHeaders(token, { "Content-Type": "application/json" }),
    body: JSON.stringify({}),
  });
}

export function createRun(
  serviceKey: string,
  ticket: { ticket_id: string; subject: string; body: string },
): Promise<RunResult> {
  return request<RunResult>("/v1/runs", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": serviceKey,
    },
    body: JSON.stringify({ ticket }),
  });
}

export function readRun(serviceKey: string, traceId: string): Promise<RunResult> {
  return request<RunResult>(`/v1/runs/${encodeURIComponent(traceId)}`, {
    headers: { "X-API-Key": serviceKey },
  });
}

export async function waitForRun(
  serviceKey: string,
  traceId: string,
  attempts = 240,
): Promise<RunResult | null> {
  for (let i = 0; i < attempts; i += 1) {
    const body = await readRun(serviceKey, traceId);
    if (body.status !== "running") return body;
    await new Promise((r) => setTimeout(r, 500));
  }
  return null;
}

export const AUTH_ERROR_ZH: Record<string, string> = {
  review_auth_not_configured: "服务还没有配置 REVIEW_API_KEY。",
  review_auth_failed: "审核引导密钥不正确。",
  review_token_missing: "请先登录领取人员令牌。",
  review_token_expired: "人员令牌已过期，请重新登录。",
  review_jwt_not_configured: "服务还没有配置审核 JWT。",
  review_mint_disabled: "OIDC 已开启，mint 已关闭，请使用 IdP 令牌。",
  review_oidc_jwks: "无法拉取 IdP JWKS，请检查 REVIEW_OIDC_*。",
  review_token_invalid: "人员令牌无效，请重新登录。",
  service_auth_not_configured: "服务还没有配置 SERVICE_API_KEY。",
  service_auth_failed: "受理密钥不正确。",
};

export function authMessage(code: string, mode: "mint" | "oidc" = "mint"): string {
  if (code === "review_token_missing" && mode === "oidc") {
    return "请先粘贴 IdP 人员令牌。";
  }
  return AUTH_ERROR_ZH[code] || "人员令牌无效，请重新登录。";
}
