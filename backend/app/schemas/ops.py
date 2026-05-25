from pydantic import BaseModel, Field


class MetricSampleOut(BaseModel):
    name: str
    labels: dict[str, str] = Field(default_factory=dict)
    value: float


class OpsSummaryResponse(BaseModel):
    health_status: str
    ready_status: str
    mode: str
    audit_enabled: bool
    readiness_missing: list[str] = Field(default_factory=list)
    metrics: list[MetricSampleOut] = Field(default_factory=list)
