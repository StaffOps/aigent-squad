"""Agent configuration schema — defines what an agent IS via YAML."""
from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Agentic loop budget defaults (B4 — spec 37, Decision 5).
# Env-overridable; enforcement happens in Phase 3 (agentic loop).
#
# SCALE cost/latency tradeoff (spec 37 scale requirement):
#   MAX_TOOL_RESULT_CHARS (40000 ≈ 10K tokens) defines the SAMPLE size the
#   model sees from a single tool result — NOT a way to ingest entire lists.
#   The true total count comes from the truncation marker (e.g. "267 items
#   total"); specifics come from follow-up filtered queries. This gives a rich
#   sample for pattern recognition while keeping per-call cost bounded.
#
#   MAX_LOOP_TOKENS (300000) provides headroom for a few large-sample results
#   + reasoning within the ~200K context window.  Still cost-conscious: a
#   typical 3-tool loop uses ~30K tokens; the 150K ceiling is for complex
#   multi-step investigations, not routine queries.
#
#   MAX_LOOP_DURATION_MS (120000) accommodates the slower Converse turns that
#   naturally result from larger context windows.
# ---------------------------------------------------------------------------

MAX_TOOL_STEPS: int = int(os.environ.get("AIGENT_MAX_TOOL_STEPS", "8"))
MAX_LOOP_DURATION_MS: int = int(os.environ.get("AIGENT_MAX_LOOP_DURATION_MS", "120000"))
MAX_LOOP_TOKENS: int = int(os.environ.get("AIGENT_MAX_LOOP_TOKENS", "300000"))
MAX_TOOL_RESULT_CHARS: int = int(os.environ.get("AIGENT_MAX_TOOL_RESULT_CHARS", "40000"))

# ---------------------------------------------------------------------------
# Context-trimming config (spec 40, DC4/DC5).
# Keep last N tool-result turns verbatim; older results get a deterministic
# enriched summary (shape + sample + keys).  Reduces per-turn context size so
# MAX_LOOP_TOKENS is not exhausted and latency drops.
# ---------------------------------------------------------------------------

CONTEXT_TRIM_ENABLED: bool = os.environ.get("AIGENT_CONTEXT_TRIM_ENABLED", "true").lower() in ("true", "1", "yes")
CONTEXT_KEEP_LAST_N: int = max(int(os.environ.get("AIGENT_CONTEXT_KEEP_LAST_N", "5")), 1)


class DatasourceConfig(BaseModel):
    type: str  # boto3, kubernetes, http, athena, mcp
    name: str = ""
    services: list[str] = []  # boto3 services
    resources: list[str] = []  # k8s resources
    url: str = ""  # http endpoint / mcp server URL
    headers: dict[str, str] = {}
    database: str = ""  # athena
    table: str = ""
    workgroup: str = "primary"
    # mcp: read-only tool allowlist (fail-closed — empty = no tools callable)
    tools: list[str] = []
    tool_arguments: dict[str, str] = {}  # static args merged into each tool call
    inject_query_as: str = ""  # if set, the user query is passed under this arg key; else not passed
    transport: Literal["sse", "streamable-http"] = "streamable-http"  # mcp transport: 'streamable-http' (default) or 'sse' (legacy, explicit opt-in)


class CacheConfig(BaseModel):
    ttl: int = 300
    namespace: str = ""


class ModelConfig(BaseModel):
    tier: str = "standard"  # fast | standard | premium
    temperature: float = 0.1


class DelegateConfig(BaseModel):
    agent: str
    when: str


class AgentConfig(BaseModel):
    """Schema for agents/<name>/agent.yaml"""
    name: str
    description: str
    domain: str
    capabilities: list[str]
    datasources: list[DatasourceConfig] = []
    skills: list[str] = []  # global skill names this agent may use (lazy-loaded)
    routing_keywords: list[str] = []
    cache: CacheConfig = Field(default_factory=CacheConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    read_only: bool = True
    evidence_types: list[str] = []
    delegates_to: list[DelegateConfig] = []
    required_env: list[str] = []
    enabled: bool = True
    port: int = 8001
