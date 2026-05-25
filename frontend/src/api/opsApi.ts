import type {
  AuditFilters,
  AuditSessionListResponse,
  AuditStats,
  AuditTurn,
  AuditTurnListResponse,
  OpsSummary,
} from "../types/ops";

const API_KEY_STORAGE = "voice_robot_admin_api_key";

export function getStoredApiKey(): string {
  return sessionStorage.getItem(API_KEY_STORAGE) ?? "";
}

export function saveApiKey(key: string): void {
  sessionStorage.setItem(API_KEY_STORAGE, key.trim());
}

function apiBase(): string {
  return import.meta.env.VITE_API_BASE ?? "";
}

async function adminFetch<T>(path: string, params: Record<string, string> = {}): Promise<T> {
  const key = getStoredApiKey();
  if (!key) {
    throw new Error("请先填写并保存 Admin API Key");
  }
  const qs = new URLSearchParams(params);
  const url = `${apiBase()}${path}${qs.toString() ? `?${qs.toString()}` : ""}`;
  const response = await fetch(url, { headers: { "X-Admin-Api-Key": key } });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new Error(body.detail ?? response.statusText);
  }
  return response.json() as Promise<T>;
}

export async function fetchOpsSummary(): Promise<OpsSummary> {
  return adminFetch<OpsSummary>("/admin/ops/summary");
}

export async function fetchAuditStats(filters: AuditFilters): Promise<AuditStats> {
  return adminFetch<AuditStats>("/admin/audit/stats", buildAuditParams(filters));
}

export async function fetchAuditSessions(
  filters: AuditFilters,
  limit: number,
  offset: number,
): Promise<AuditSessionListResponse> {
  return adminFetch<AuditSessionListResponse>("/admin/audit/sessions", {
    ...buildAuditParams(filters),
    limit: String(limit),
    offset: String(offset),
  });
}

export async function fetchSessionTurns(sessionId: string): Promise<AuditTurnListResponse> {
  return adminFetch<AuditTurnListResponse>(`/admin/audit/sessions/${encodeURIComponent(sessionId)}/turns`);
}

export async function fetchAuditTurns(
  filters: AuditFilters,
  limit: number,
  offset: number,
): Promise<AuditTurnListResponse> {
  return adminFetch<AuditTurnListResponse>("/admin/audit/turns", {
    ...buildAuditParams(filters),
    limit: String(limit),
    offset: String(offset),
  });
}

export async function fetchAuditTurn(turnPk: number): Promise<AuditTurn> {
  return adminFetch<AuditTurn>(`/admin/audit/turns/${turnPk}`);
}

export async function exportAuditCsv(filters: AuditFilters): Promise<void> {
  const key = getStoredApiKey();
  if (!key) {
    throw new Error("请先填写并保存 Admin API Key");
  }
  const qs = new URLSearchParams(buildAuditParams(filters));
  const response = await fetch(`${apiBase()}/admin/audit/export.csv?${qs.toString()}`, {
    headers: { "X-Admin-Api-Key": key },
  });
  if (!response.ok) {
    throw new Error("导出失败");
  }
  const blob = await response.blob();
  const anchor = document.createElement("a");
  anchor.href = URL.createObjectURL(blob);
  anchor.download = "audit_export.csv";
  anchor.click();
  URL.revokeObjectURL(anchor.href);
}

function buildAuditParams(filters: AuditFilters): Record<string, string> {
  const params: Record<string, string> = {};
  if (filters.sessionId.trim()) {
    params.session_id = filters.sessionId.trim();
  }
  if (filters.status) {
    params.status = filters.status;
  }
  if (filters.inputMode) {
    params.input_mode = filters.inputMode;
  }
  if (filters.from) {
    params.from = filters.from;
  }
  if (filters.to) {
    params.to = filters.to;
  }
  return params;
}
