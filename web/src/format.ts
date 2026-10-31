export const STATUS_LABEL: Record<string, string> = {
  resolved: "已结案",
  escalated: "已升级",
  pending: "重新送审",
  open: "新建",
  awaiting_human: "待审",
};

export const CATEGORY_LABEL: Record<string, string> = {
  billing: "账单",
  technical: "技术",
  account: "账户",
  other: "其他",
};

export const PRIORITY_LABEL: Record<string, string> = {
  low: "低",
  medium: "中",
  high: "高",
  critical: "紧急",
};

export const NODE_LABEL: Record<string, string> = {
  ingest: "接入",
  classify: "分类",
  act: "调度",
  draft: "起草",
  hitl_gate: "人审",
  finalize: "结案",
};

export function categoryLabel(value?: string | null): string {
  if (!value) return "未分类";
  return CATEGORY_LABEL[value] || value;
}

export function priorityLabel(value?: string | null): string {
  if (!value) return "未分级";
  return PRIORITY_LABEL[value] || value;
}

export function statusLabel(value?: string | null): string {
  if (!value) return "未知";
  return STATUS_LABEL[value] || value;
}

export function countLabel(count: number, hasMore: boolean, loading: boolean): string {
  if (loading && count === 0) return "…";
  if (hasMore) return `${count}+`;
  return String(count);
}

export function readJwtClaims(token: string): { sub?: string; exp?: number } {
  try {
    const part = token.split(".")[1];
    if (!part) return {};
    const padded = part.replace(/-/g, "+").replace(/_/g, "/");
    const binary = atob(padded.padEnd(Math.ceil(padded.length / 4) * 4, "="));
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    const json = JSON.parse(new TextDecoder().decode(bytes)) as {
      sub?: unknown;
      exp?: unknown;
    };
    return {
      sub: typeof json.sub === "string" ? json.sub : undefined,
      exp: typeof json.exp === "number" ? json.exp : undefined,
    };
  } catch {
    return {};
  }
}

export function avatarText(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return "?";
  return trimmed.slice(0, 1).toUpperCase();
}

export function formatExpiry(exp?: number, now = Date.now()): string {
  if (!exp) return "人员令牌";
  const delta = exp * 1000 - now;
  if (delta <= 0) return "令牌已过期";
  const minutes = Math.max(1, Math.round(delta / 60000));
  if (minutes < 60) return `${minutes} 分钟后过期`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (rest === 0) return `${hours} 小时后过期`;
  return `${hours} 小时 ${rest} 分钟后过期`;
}

export function formatRelative(iso?: string, now = Date.now()): string {
  if (!iso) return "";
  const date = new Date(iso);
  const time = date.getTime();
  if (Number.isNaN(time)) return iso;
  const delta = now - time;
  if (delta < 0) return formatAbsolute(iso);
  const minutes = Math.round(delta / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days} 天前`;
  return formatAbsolute(iso);
}

export function formatAbsolute(iso?: string): string {
  if (!iso) return "时间未知";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("zh-CN", { hour12: false });
}

export function closedQueryOnServer(query: string): boolean {
  return query.trim().length >= 3;
}

export function spanTone(status: string): "ok" | "bad" {
  const value = status.toLowerCase();
  if (value === "error" || value === "failed" || value.includes("error")) return "bad";
  return "ok";
}
