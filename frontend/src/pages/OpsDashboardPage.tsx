import React, { useCallback, useEffect, useMemo, useState } from "react";

import {
  exportAuditCsv,
  fetchAuditSessions,
  fetchAuditStats,
  fetchAuditTurn,
  fetchOpsSummary,
  fetchSessionTurns,
  getStoredApiKey,
  saveApiKey,
} from "../api/opsApi";
import type { AuditFilters, AuditSession, AuditStats, AuditTurn, MetricSample, OpsSummary } from "../types/ops";
import "./OpsDashboardPage.css";

const PAGE_SIZE = 30;
const EMPTY_FILTERS: AuditFilters = { sessionId: "", status: "", inputMode: "", from: "", to: "" };

type TabId = "observability" | "audit";

type SessionModalState = {
  sessionId: string;
  turns: AuditTurn[];
  loading: boolean;
};

function fmtTime(iso: string): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("zh-CN", { hour12: false });
}

function trunc(text: string, max: number): string {
  if (!text) return "";
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function metricValue(metrics: MetricSample[], name: string, labels?: Record<string, string>): number {
  const row = metrics.find((item) => {
    if (item.name !== name) return false;
    if (!labels) return true;
    return Object.entries(labels).every(([key, value]) => item.labels[key] === value);
  });
  return row?.value ?? 0;
}

function sumMetricsByPrefix(metrics: MetricSample[], prefix: string): number {
  return metrics.filter((item) => item.name === prefix || item.name.startsWith(`${prefix}_`)).reduce((acc, item) => acc + item.value, 0);
}

export function OpsDashboardPage(): JSX.Element {
  const [tab, setTab] = useState<TabId>("observability");
  const [apiKeyDraft, setApiKeyDraft] = useState(getStoredApiKey);
  const [statusText, setStatusText] = useState("请保存 API Key 后加载");
  const [statusError, setStatusError] = useState(false);

  const [opsSummary, setOpsSummary] = useState<OpsSummary | null>(null);
  const [auditStats, setAuditStats] = useState<AuditStats | null>(null);
  const [filters, setFilters] = useState<AuditFilters>(EMPTY_FILTERS);
  const [sessions, setSessions] = useState<AuditSession[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [sessionModal, setSessionModal] = useState<SessionModalState | null>(null);
  const [turnDetail, setTurnDetail] = useState<AuditTurn | null>(null);

  const loadObservability = useCallback(async () => {
    const summary = await fetchOpsSummary();
    setOpsSummary(summary);
  }, []);

  const loadAudit = useCallback(async () => {
    const [stats, list] = await Promise.all([
      fetchAuditStats(filters),
      fetchAuditSessions(filters, PAGE_SIZE, offset),
    ]);
    setAuditStats(stats);
    setSessions(list.items);
    setTotal(list.total);
  }, [filters, offset]);

  const refreshAll = useCallback(async () => {
    setStatusError(false);
    setStatusText("加载中…");
    try {
      await loadObservability();
      if (tab === "audit") {
        await loadAudit();
      }
      setStatusText(`已更新 · ${new Date().toLocaleTimeString("zh-CN")}`);
    } catch (error) {
      setStatusError(true);
      setStatusText(error instanceof Error ? error.message : String(error));
    }
  }, [loadObservability, loadAudit, tab]);

  useEffect(() => {
    if (!getStoredApiKey()) return;
    void refreshAll();
  }, [refreshAll]);

  useEffect(() => {
    if (!getStoredApiKey() || tab !== "audit") return;
    void (async () => {
      try {
        await loadAudit();
      } catch (error) {
        setStatusError(true);
        setStatusText(error instanceof Error ? error.message : String(error));
      }
    })();
  }, [tab, loadAudit]);

  const observabilityCards = useMemo(() => {
    if (!opsSummary) return [];
    const metrics = opsSummary.metrics;
    const turnOk =
      metricValue(metrics, "voice_turn_total", { status: "ok", input_mode: "voice" }) +
      metricValue(metrics, "voice_turn_total", { status: "ok", input_mode: "text" });
    const turnErr =
      metricValue(metrics, "voice_turn_total", { status: "error", input_mode: "voice" }) +
      metricValue(metrics, "voice_turn_total", { status: "error", input_mode: "text" });
    return [
      ["服务就绪", opsSummary.ready_status === "ready" ? "ready" : "not_ready"],
      ["运行模式", opsSummary.mode],
      ["审计落库", opsSummary.audit_enabled ? "已开启" : "未开启"],
      ["WS 连接", String(metricValue(metrics, "voice_ws_connections_active"))],
      ["成功轮次", String(turnOk)],
      ["失败轮次", String(turnErr)],
      ["知识库工具", String(sumMetricsByPrefix(metrics, "voice_agent_tool_calls_total"))],
    ] as const;
  }, [opsSummary]);

  const auditCards = useMemo(() => {
    if (!auditStats) return [];
    return [
      ["总会话", String(auditStats.unique_sessions)],
      ["总轮次", String(auditStats.total_turns)],
      ["成功", String(auditStats.ok_count)],
      ["失败", String(auditStats.error_count)],
      ["平均首包(ms)", String(auditStats.avg_first_token_ms)],
      ["平均端到端(ms)", String(auditStats.avg_latency_ms)],
      ["工具调用率", `${(auditStats.tool_called_rate * 100).toFixed(1)}%`],
      ["知识库命中率", `${(auditStats.wiki_hit_rate * 100).toFixed(1)}%`],
    ] as const;
  }, [auditStats]);

  const onSaveKey = (): void => {
    saveApiKey(apiKeyDraft);
    void refreshAll();
  };

  const onSearchAudit = (): void => {
    setOffset(0);
    void loadAudit().catch((error) => {
      setStatusError(true);
      setStatusText(error instanceof Error ? error.message : String(error));
    });
  };

  const openSession = async (sessionId: string): Promise<void> => {
    setSessionModal({ sessionId, turns: [], loading: true });
    setTurnDetail(null);
    try {
      const data = await fetchSessionTurns(sessionId);
      setSessionModal({ sessionId, turns: data.items, loading: false });
    } catch (error) {
      setSessionModal(null);
      setStatusError(true);
      setStatusText(error instanceof Error ? error.message : String(error));
    }
  };

  const openTurnDetail = async (turn: AuditTurn): Promise<void> => {
    try {
      const row = await fetchAuditTurn(turn.id);
      setTurnDetail(row);
    } catch {
      setTurnDetail(turn);
    }
  };

  const closeModals = (): void => {
    setSessionModal(null);
    setTurnDetail(null);
  };

  const backToSessionList = (): void => {
    setTurnDetail(null);
  };

  return (
    <div className="ops-page">
      <div className="ops-toolbar">
        <label>
          Admin API Key
          <input
            type="password"
            value={apiKeyDraft}
            onChange={(e) => setApiKeyDraft(e.target.value)}
            placeholder="与 VOICE_ROBOT_AUDIT_ADMIN_API_KEY 一致"
            style={{ minWidth: 220 }}
          />
        </label>
        <button type="button" onClick={onSaveKey}>
          保存并刷新
        </button>
      </div>

      <div className={`ops-status${statusError ? " error" : ""}`}>{statusText}</div>

      <div className="ops-tabs">
        <button type="button" className={tab === "observability" ? "active" : ""} onClick={() => setTab("observability")}>
          可观测
        </button>
        <button type="button" className={tab === "audit" ? "active" : ""} onClick={() => setTab("audit")}>
          可审计
        </button>
      </div>

      {tab === "observability" && (
        <>
          <div className="ops-cards">
            {observabilityCards.map(([label, value]) => (
              <div className="ops-card" key={label}>
                <div className="label">{label}</div>
                <div className="value">{value}</div>
              </div>
            ))}
          </div>
          {opsSummary && opsSummary.readiness_missing.length > 0 && (
            <p className="ops-status error">缺失配置：{opsSummary.readiness_missing.join(", ")}</p>
          )}
          <h3 style={{ fontSize: "0.95rem", color: "var(--muted)" }}>Prometheus 指标快照</h3>
          <ul className="ops-metric-list">
            {(opsSummary?.metrics ?? []).slice(0, 40).map((item, index) => (
              <li key={`${item.name}-${index}`}>
                {item.name}
                {Object.keys(item.labels).length ? ` ${JSON.stringify(item.labels)}` : ""} = {item.value}
              </li>
            ))}
          </ul>
        </>
      )}

      {tab === "audit" && (
        <>
          <div className="ops-cards">
            {auditCards.map(([label, value]) => (
              <div className="ops-card" key={label}>
                <div className="label">{label}</div>
                <div className="value">{value}</div>
              </div>
            ))}
          </div>

          <div className="ops-toolbar">
            <label>
              会话 ID
              <input value={filters.sessionId} onChange={(e) => setFilters({ ...filters, sessionId: e.target.value })} />
            </label>
            <label>
              状态
              <select value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
                <option value="">全部</option>
                <option value="ok">ok</option>
                <option value="error">error</option>
              </select>
            </label>
            <label>
              输入模式
              <select value={filters.inputMode} onChange={(e) => setFilters({ ...filters, inputMode: e.target.value })}>
                <option value="">全部</option>
                <option value="voice">voice</option>
                <option value="text">text</option>
              </select>
            </label>
            <label>
              开始
              <input type="date" value={filters.from} onChange={(e) => setFilters({ ...filters, from: e.target.value })} />
            </label>
            <label>
              结束
              <input type="date" value={filters.to} onChange={(e) => setFilters({ ...filters, to: e.target.value })} />
            </label>
            <button type="button" onClick={onSearchAudit}>
              查询
            </button>
            <button
              type="button"
              className="secondary"
              onClick={() => exportAuditCsv(filters).catch((error) => alert(error.message))}
            >
              导出 CSV
            </button>
          </div>

          <div className="ops-table-wrap">
            <table className="ops-table">
              <thead>
                <tr>
                  <th>会话 ID</th>
                  <th>轮次数</th>
                  <th>成功/失败</th>
                  <th>平均首包(ms)</th>
                  <th>平均端到端(ms)</th>
                  <th>最近时间</th>
                  <th>最近输入</th>
                  <th>最近问题</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {sessions.map((row) => (
                  <tr key={row.session_id}>
                    <td>
                      <button
                        type="button"
                        className="ops-link"
                        title={row.session_id}
                        onClick={() => void openSession(row.session_id)}
                      >
                        {trunc(row.session_id, 20)}
                      </button>
                    </td>
                    <td>{row.turn_count}</td>
                    <td>
                      {row.ok_count}/{row.error_count}
                    </td>
                    <td>{row.avg_first_token_ms}</td>
                    <td>{row.avg_latency_ms}</td>
                    <td>{fmtTime(row.last_turn_at)}</td>
                    <td>
                      <span className="ops-badge mode">{row.last_input_mode || "-"}</span>
                    </td>
                    <td title={row.last_user_text}>{trunc(row.last_user_text, 32)}</td>
                    <td>
                      <button type="button" className="ops-link" onClick={() => void openSession(row.session_id)}>
                        详情
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="ops-pager">
            <button
              type="button"
              className="secondary"
              disabled={offset <= 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              上一页
            </button>
            <span>
              {total === 0 ? "0" : `${offset + 1}-${Math.min(offset + PAGE_SIZE, total)}`} / {total}
            </span>
            <button
              type="button"
              className="secondary"
              disabled={offset + PAGE_SIZE >= total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              下一页
            </button>
          </div>
        </>
      )}

      {(sessionModal || turnDetail) && (
        <div className="ops-modal-backdrop" onClick={closeModals}>
          <div className="ops-modal" onClick={(e) => e.stopPropagation()}>
            {turnDetail ? (
              <>
                <h3>
                  轮次 #{turnDetail.id} · {turnDetail.turn_id}
                </h3>
                <p>
                  <strong>trace_id</strong> {turnDetail.trace_id}
                </p>
                <p>
                  <strong>首包延迟</strong> {turnDetail.llm_first_token_ms} ms · <strong>端到端</strong>{" "}
                  {turnDetail.latency_ms_e2e} ms · <strong>状态</strong> {turnDetail.status}
                </p>
                <p>
                  <strong>检索说明</strong> {(turnDetail.retrieval_notes ?? []).join("；") || "（无）"}
                </p>
                <p>
                  <strong>知识库来源</strong>
                </p>
                <ul>
                  {(turnDetail.wiki_sources ?? []).map((path) => (
                    <li key={path}>{path}</li>
                  ))}
                </ul>
                <p>
                  <strong>用户</strong>
                </p>
                <pre>{turnDetail.user_text || ""}</pre>
                <p>
                  <strong>助手</strong>
                </p>
                <pre>{turnDetail.assistant_text || ""}</pre>
                {turnDetail.error_code && (
                  <p>
                    <strong>错误码</strong> {turnDetail.error_code}
                  </p>
                )}
                <div className="ops-modal-actions">
                  <button type="button" className="secondary" onClick={backToSessionList}>
                    返回交互列表
                  </button>
                  <button type="button" className="secondary" onClick={closeModals}>
                    关闭
                  </button>
                </div>
              </>
            ) : sessionModal ? (
              <>
                <h3>会话 · {sessionModal.sessionId}</h3>
                <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>点击某一轮查看完整问答与检索依据</p>
                {sessionModal.loading ? (
                  <p>加载中…</p>
                ) : sessionModal.turns.length === 0 ? (
                  <p>暂无交互记录</p>
                ) : (
                  <ul className="ops-turn-list">
                    {sessionModal.turns.map((turn, index) => (
                      <li key={turn.id} onClick={() => void openTurnDetail(turn)}>
                        <div>
                          <strong>
                            第 {index + 1} 轮
                          </strong>{" "}
                          <span className={`ops-badge ${turn.status === "ok" ? "ok" : "err"}`}>{turn.status}</span>{" "}
                          <span className="ops-badge mode">{turn.input_mode}</span>
                        </div>
                        <div>{trunc(turn.user_text, 80) || "（空）"}</div>
                        <div className="meta">
                          {fmtTime(turn.created_at)} · 首包 {turn.llm_first_token_ms} ms · 端到端 {turn.latency_ms_e2e}{" "}
                          ms
                          {turn.tool_called ? " · 已查知识库" : ""}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
                <div className="ops-modal-actions">
                  <button type="button" className="secondary" onClick={closeModals}>
                    关闭
                  </button>
                </div>
              </>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}
