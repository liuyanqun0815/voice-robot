"""Prometheus 指标（Phase 1）。"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Gauge, Histogram, generate_latest

WS_CONNECTIONS_ACTIVE = Gauge(
    "voice_ws_connections_active",
    "Current active WebSocket connections on /ws/voice",
)

TURN_TOTAL = Counter(
    "voice_turn_total",
    "Committed conversation turns",
    labelnames=("status", "input_mode"),
)

LLM_FIRST_TOKEN_SECONDS = Histogram(
    "voice_llm_first_token_seconds",
    "Time from LLM invoke to first assistant token",
    labelnames=("input_mode",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 40.0),
)

TURN_DURATION_SECONDS = Histogram(
    "voice_turn_duration_seconds",
    "Turn pipeline duration in seconds",
    labelnames=("stage",),
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 40.0, 60.0),
)

WIKI_TOOL_CALLS_TOTAL = Counter(
    "voice_agent_tool_calls_total",
    "Agent tool invocations",
    labelnames=("tool",),
)

WIKI_RETRIEVAL_NOTES_TOTAL = Counter(
    "voice_wiki_retrieval_notes_total",
    "Wiki retrieval channel hits",
    labelnames=("note",),
)


def metrics_response_body() -> bytes:
    return generate_latest()


def metrics_content_type() -> str:
    return CONTENT_TYPE_LATEST


def collect_metrics_snapshot() -> list[dict[str, object]]:
    """导出当前 Prometheus 指标快照，供前端运维仪表盘使用。"""
    rows: list[dict[str, object]] = []
    for family in REGISTRY.collect():
        for sample in family.samples:
            rows.append(
                {
                    "name": sample.name,
                    "labels": dict(sample.labels),
                    "value": float(sample.value),
                }
            )
    return rows
