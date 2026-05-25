export type AuditTurn = {
  id: number;
  trace_id: string;
  session_id: string;
  turn_id: string;
  input_mode: string;
  user_text: string;
  assistant_text: string;
  wiki_sources: string[];
  retrieval_notes: string[];
  tool_called: boolean;
  agent_thread_id: string;
  latency_ms_e2e: number;
  llm_first_token_ms: number;
  status: string;
  error_code: string | null;
  created_at: string;
};

export type AuditSession = {
  session_id: string;
  turn_count: number;
  ok_count: number;
  error_count: number;
  avg_latency_ms: number;
  avg_first_token_ms: number;
  tool_called_count: number;
  first_turn_at: string;
  last_turn_at: string;
  last_input_mode: string;
  last_user_text: string;
};

export type AuditSessionListResponse = {
  total: number;
  limit: number;
  offset: number;
  items: AuditSession[];
};

export type AuditTurnListResponse = {
  total: number;
  limit: number;
  offset: number;
  items: AuditTurn[];
};

export type AuditStats = {
  total_turns: number;
  ok_count: number;
  error_count: number;
  avg_latency_ms: number;
  avg_first_token_ms: number;
  tool_called_count: number;
  tool_called_rate: number;
  wiki_hit_count: number;
  wiki_hit_rate: number;
  unique_sessions: number;
  by_input_mode: Record<string, number>;
  by_status: Record<string, number>;
};

export type MetricSample = {
  name: string;
  labels: Record<string, string>;
  value: number;
};

export type OpsSummary = {
  health_status: string;
  ready_status: string;
  mode: string;
  audit_enabled: boolean;
  readiness_missing: string[];
  metrics: MetricSample[];
};

export type AuditFilters = {
  sessionId: string;
  status: string;
  inputMode: string;
  from: string;
  to: string;
};
