"""Custom business metrics for AIgent-squad using otel-helper."""
from otel_helper import get_meter

meter = get_meter("aigent-squad")


# ---------------------------------------------------------------------------
# Helpers: cardinality-safe label bucketing
# ---------------------------------------------------------------------------


def bucketize_confidence(value: float) -> str:
    """Bucketize a float confidence score into a bounded label set.

    Returns one of {"high", "medium", "low"} — 3 series max per parent metric.
    Prevents unbounded cardinality from raw float labels (e.g. 0.8723, 0.9001).

    Thresholds:
      - high   : >= 0.85  (strong signal, few contradictions)
      - medium : >= 0.50  (some evidence, ambiguous)
      - low    : <  0.50  (insufficient evidence)
    """
    if value >= 0.85:
        return "high"
    elif value >= 0.50:
        return "medium"
    else:
        return "low"

# RED metrics (Request, Error, Duration) per agent
request_counter = meter.create_counter(
    name="aigent.requests.total",
    description="Total requests processed per agent",
    unit="1",
)

error_counter = meter.create_counter(
    name="aigent.errors.total",
    description="Total errors per agent",
    unit="1",
)

request_duration = meter.create_histogram(
    name="aigent.request.duration",
    description="Request processing duration per agent",
    unit="ms",
)

# Token/cost metrics
token_counter = meter.create_counter(
    name="aigent.tokens.total",
    description="Total tokens consumed per agent (input + output)",
    unit="1",
)

estimated_cost = meter.create_counter(
    name="aigent.cost.estimated",
    description="Estimated cost in USD per agent",
    unit="USD",
)

# === Spec 38: model-tier routing (pertinent signal — tier distribution) ===
# ONE counter, 3 series (fast/standard/deep). Answers "what % of queries route to
# deep (Opus)?" directly from VictoriaMetrics without a LogQL hack. Effective model
# invocation is already covered by aigent.tokens.total / aigent.cost.estimated{model}.
tier_routing_decisions = meter.create_counter(
    name="aigent.tier.routing_decisions",
    description="Model-tier routing decisions, labeled by resolved tier (fast/standard/deep)",
    unit="1",
)

# === Spec 10: Efficiency (where do time and tokens go?) ===
collect_duration = meter.create_histogram(
    name="aigent.collect.duration",
    description="Time spent collecting datasource context (adapter fan-out) per agent",
    unit="ms",
)

llm_duration = meter.create_histogram(
    name="aigent.llm.duration",
    description="Bedrock round-trip latency per call (excludes retry backoff)",
    unit="ms",
)

prompt_size_tokens = meter.create_histogram(
    name="aigent.prompt.size_tokens",
    description="Distribution of Bedrock-reported input tokens per call (detect prompt bloat)",
    unit="1",
)

# === Spec 10: Quality (investigation depth) ===
investigation_rounds = meter.create_histogram(
    name="aigent.investigation.rounds",
    description="Rounds completed per RCA investigation (vs cost cap)",
    unit="1",
)

# Cache metrics
cache_hits = meter.create_counter(
    name="aigent.cache.hits",
    description="Cache hits (data cache only)",
    unit="1",
)

cache_misses = meter.create_counter(
    name="aigent.cache.misses",
    description="Cache misses",
    unit="1",
)

# === Spec 06: Resilience patterns ===
circuit_breaker_transitions = meter.create_counter(
    name="aigent.circuit_breaker.transitions",
    description="Circuit breaker state transitions",
    unit="1",
)

# === Spec 31: Edge gateway + worker pool ===
gateway_pool_rejections = meter.create_counter(
    name="aigent.gateway.pool_rejections",
    description="Requests rejected with 503 because the worker pool was at capacity",
    unit="1",
)

gateway_pool_depth = meter.create_up_down_counter(
    name="aigent.gateway.pool_depth",
    description="In-flight jobs currently held by the gateway worker pool",
    unit="1",
)

gateway_queue_wait = meter.create_histogram(
    name="aigent.gateway.queue_wait",
    description="Time a job waited to acquire a worker pool slot",
    unit="ms",
)

gateway_redis_fallback_active = meter.create_counter(
    name="aigent.gateway.redis_fallback_active",
    description="Times job lifecycle fell back to log-only because Redis was unavailable",
    unit="1",
)

# F-009: edge input-size caps and job timeouts. Labels are bounded enums.
gateway_input_rejections = meter.create_counter(
    name="aigent.gateway.input_rejections",
    description="Requests rejected with 413 by the gateway input-size caps (reason=body_bytes|input_chars|messages)",
    unit="1",
)

gateway_timeouts = meter.create_counter(
    name="aigent.gateway.timeouts",
    description="Gateway jobs aborted by a timeout (kind=first_byte|idle|job)",
    unit="1",
)

rate_limit_blocks = meter.create_counter(
    name="aigent.rate_limit.blocks",
    description="Requests blocked by admission guards (rate or budget)",
    unit="1",
)

# === Spec 25: Bedrock concurrency semaphore ===
bedrock_queue_depth = meter.create_up_down_counter(
    name="aigent.bedrock.queue_depth",
    description="Current number of requests waiting for a Bedrock semaphore slot",
    unit="1",
)

bedrock_queue_wait = meter.create_histogram(
    name="aigent.bedrock.queue_wait_seconds",
    description="Time spent waiting for a Bedrock semaphore slot",
    unit="s",
)

# === Spec 17: Multi-agent fan-out ===
fanout_calls = meter.create_counter(
    name="aigent.fanout.calls",
    description="Fan-out invocations (N>=2 agents)",
    unit="1",
)

fanout_agents_consulted = meter.create_histogram(
    name="aigent.fanout.agents_consulted",
    description="Number of agents consulted per fan-out",
    unit="1",
)

fanout_agents_failed = meter.create_counter(
    name="aigent.fanout.agents_failed",
    description="Agents that failed during fan-out",
    unit="1",
)

synthesizer_calls = meter.create_counter(
    name="aigent.synthesizer.calls",
    description="Synthesizer invocations (RCA + fan-out merge)",
    unit="1",
)

# === Spec 18: RCA Investigation ===
investigation_started = meter.create_counter(
    name="aigent.investigation.started",
    description="Investigations triggered (mode=investigate or auto-detected)",
    unit="1",
)

investigation_completed = meter.create_counter(
    name="aigent.investigation.completed",
    description="Investigations completed, labeled by confidence level",
    unit="1",
)

investigation_duration = meter.create_histogram(
    name="aigent.investigation.duration",
    description="End-to-end investigation duration",
    unit="ms",
)

investigation_evidence_count = meter.create_histogram(
    name="aigent.investigation.evidence_count",
    description="Total evidence items collected per investigation",
    unit="1",
)

# === Spec 21: KB / Distillation / RAG ===
kb_distillation_cost = meter.create_counter(
    name="aigent.kb.distillation.cost",
    description="Estimated USD cost of distillation pipeline",
    unit="USD",
)

kb_items_created = meter.create_counter(
    name="aigent.kb.items_created",
    description="KB items created, labeled by type and status",
    unit="1",
)

kb_rag_queries = meter.create_counter(
    name="aigent.kb.rag.queries",
    description="RAG injection lookups against KB",
    unit="1",
)

kb_rag_hits = meter.create_counter(
    name="aigent.kb.rag.hits",
    description="RAG queries that returned at least 1 similar case above threshold",
    unit="1",
)

kb_budget_exhausted = meter.create_counter(
    name="aigent.kb.budget.exhausted",
    description="Times the monthly KB budget was exceeded (distillation skipped)",
    unit="1",
)

# === Spec 18 Phase 2: Alert ingestion ===
alerts_received = meter.create_counter(
    name="aigent.alerts.received",
    description="Alertmanager alerts received via webhook",
    unit="1",
)

alerts_deduplicated = meter.create_counter(
    name="aigent.alerts.deduplicated",
    description="Alerts skipped due to fingerprint match within dedup window",
    unit="1",
)

alerts_investigation_triggered = meter.create_counter(
    name="aigent.alerts.investigation_triggered",
    description="Investigations triggered from alerts",
    unit="1",
)

alerts_postback = meter.create_counter(
    name="aigent.alerts.postback",
    description="Slack post-back attempts (RCA result returned to channel)",
    unit="1",
)

# === Spec 35: Response quality (T1 structural gate) ===
quality_violations = meter.create_counter(
    name="aigent.quality.violations",
    description="Structural quality defects detected in agent responses (tool-scaffolding leaks, raw adapter errors) — labels: agent_id, category",
    unit="1",
)

# === Spec 35: Eval harness (T2 scored, make eval) ===
eval_score = meter.create_histogram(
    name="aigent.eval.score",
    description="Per-question eval score emitted by `make eval` (0-1, mechanical checks + judge rubric combined) — labels: suite, agent_id",
    unit="1",
)

# === Spec 35: Groundedness (requirements.md PR-05) ===
# Numeric claims are metric-only (non-blocking) — unlike resource-ID
# groundedness (folded into quality_violations, fail-closed, since a
# resource ID is never legitimately "computed"), a dollar figure or count
# CAN be a legitimate derived value (sum, average, rounding) that won't
# appear verbatim in infra_data — hard-blocking risks denying correct
# arithmetic. See src/core/response_quality.py for the full reasoning.
ungrounded_numeric_claims = meter.create_counter(
    name="aigent.quality.ungrounded_numeric_claims",
    description="Numeric claims in a response with no matching source in collected infra_data — signal only, NOT blocking (see response_quality.py) — labels: agent_id",
    unit="1",
)

# === Spec 41: Structured calibrated honesty (B-16 Phase-2) ===

# Counter: one increment per response, labeled by the derived confidence level.
# Cardinality: 3 series (high | medium | low).
quality_confidence = meter.create_counter(
    name="aigent.quality.confidence",
    description=(
        "Structured confidence level derived from groundedness scan "
        "(spec 41). Labels: level ∈ {high, medium, low}."
    ),
    unit="1",
)

# Histogram: distribution of unverified_claims count per response.
# NOTE on buckets: default SDK boundaries apply. Explicit boundaries are NOT
# settable here — opentelemetry-api 1.29.0's create_histogram() takes only
# (name, unit, description), and the MeterProvider (where a View would go) is
# owned by the otel_helper lib, not this repo. Values are 0..20 (capped), so
# the default boundaries are coarse at the low end; tightening them requires
# a View in otel_helper. Do not document buckets this code cannot produce.
quality_unverified_claims = meter.create_histogram(
    name="aigent.quality.unverified_claims_per_response",
    description=(
        "Count of DISTINCT ungrounded numeric claims per response (deduped, "
        "capped at 20). Spec 41. Resource IDs never reach here — they block "
        "in the earlier guardrail phase. Labels: agent_id."
    ),
    unit="{claims}",
)

# ===========================================================================
# NEW METRICS (observability review — bounded cardinality, zero vanity)
# ===========================================================================

# --- (a) Tool call latency + status ---
# Labels: tool_name (bounded by the read-only MCP allowlists — ~84 distinct tools across agents),
#         status ∈ {success, error, timeout} (3 values)
# Cardinality: ~252 series worst case (84 tools × 3 statuses) — well under OTel 2000/metric
tool_call_duration = meter.create_histogram(
    name="aigent.tool.call_duration",
    description=(
        "Per-tool call latency. Labels: tool_name (bounded by MCP allowlist), "
        "status (success|error|timeout). Enables P99 per tool and identification "
        "of slow/unreliable MCP servers."
    ),
    unit="ms",
)

# --- (b) Guardrail blocks ---
# Labels: source ∈ {INPUT, OUTPUT} (guardrail engine source; emitted centrally in guardrail.py),
#         agent_id (bounded by registered agents — max ~10; "ingress" for the entry guard)
# Cardinality: ~20 series worst case
guardrail_blocks = meter.create_counter(
    name="aigent.guardrail.blocks",
    description=(
        "Counter of guardrail block/redaction events. Labels: "
        "source (INPUT|OUTPUT), agent_id. "
        "Tracks security interventions without leaking payload details."
    ),
    unit="1",
)

# --- Streaming output guard (L4/L5 on the streaming path) ---
# Labels: agent_id (bounded by registered agents — max ~10),
#         action ∈ {blocked, trace_redacted}
# Cardinality: ~20 series worst case
stream_output_interventions = meter.create_counter(
    name="aigent.stream.output_interventions",
    description=(
        "Streaming-path output guard interventions. Labels: agent_id, action "
        "(blocked = final answer refused and stream ended with content_filter; "
        "trace_redacted = a reasoning/tool-result trace line hidden because it "
        "contained sensitive data). Never carries payload details."
    ),
    unit="1",
)

# --- (c) Bedrock throttles ---
# Labels: model (bounded by MODEL_PRICING table — max 3 families)
# Cardinality: 3 series
bedrock_throttles = meter.create_counter(
    name="aigent.bedrock.throttles",
    description=(
        "Bedrock ThrottlingException/429 retries. Label: model (bounded by "
        "tier model table — haiku/sonnet/opus). Signals capacity pressure "
        "before it becomes user-visible latency."
    ),
    unit="1",
)

# --- LLM provider abstraction (spec 28) ---
# Labels: provider ∈ {bedrock, openai} (LLM_PROVIDER — 2 values),
#         error_class ∈ {throttled, server_error, timeout, client_error, malformed}
# Cardinality: ≤10 series. Emitted by the common LLMService retry loop for EVERY
# provider (Bedrock throttles additionally keep aigent.bedrock.throttles{model}).
llm_provider_errors = meter.create_counter(
    name="aigent.llm.provider_errors",
    description=(
        "LLM transport errors seen by the common retry loop (each attempt). "
        "Labels: provider (bedrock|openai), error_class "
        "(throttled|server_error|timeout|client_error|malformed)."
    ),
    unit="1",
)

# Labels: provider ∈ {bedrock, openai}. Cardinality: 2 series.
# 1 = the Bedrock Guardrail (spec 14 L1) is evaluating requests; 0 = it is OFF.
# PER-POD: each supervisor process adds its own 0/1 once at startup, so a
# fleet-wide sum counts pods with L1 active — use min()/max() by pod to alert.
# It is OFF automatically whenever LLM_PROVIDER != bedrock (spec 28 Decision 10)
# or GUARDRAIL_ENABLED=false — alert on 0 in environments that require L1.
llm_guardrail_l1_active = meter.create_up_down_counter(
    name="aigent.llm.guardrail_l1_active",
    description=(
        "1 when the Bedrock Guardrail (L1) is active, 0 when inactive "
        "(non-Bedrock LLM provider or GUARDRAIL_ENABLED=false). Label: provider."
    ),
    unit="1",
)

# --- (d) Context trimmed messages ---
# Labels: agent_id (bounded by registered agents — max ~10)
# Cardinality: ~10 series
context_trimmed_messages = meter.create_counter(
    name="aigent.context.trimmed_messages",
    description=(
        "toolResult turns replaced with deterministic summaries by "
        "trim_message_history (spec 40). Label: agent_id. Indicates context "
        "pressure — rising counts correlate with longer conversations."
    ),
    unit="1",
)

# --- (e) Classifier confidence distribution ---
# Labels: tier ∈ {fast, standard, deep} (3 values)
# Cardinality: 3 series
tier_classifier_confidence = meter.create_histogram(
    name="aigent.tier.classifier_confidence",
    description=(
        "Classifier confidence score distribution per resolved tier. "
        "Label: tier (fast|standard|deep). Enables drift detection — if "
        "deep-tier confidence drops, the classifier may be degrading."
    ),
    unit="1",
)

# --- (FIX 3) Investigation fan-out error counter ---
# Labels: agent_id (bounded by registered agents)
# Cardinality: ~10 series
investigation_fanout_errors = meter.create_counter(
    name="aigent.investigation.fanout_errors",
    description=(
        "Agents that raised exceptions during RCA evidence fan-out. "
        "Label: agent_id. Distinct from fanout_agents_failed (which counts "
        "the supervisor fan-out path) — this is investigation-specific."
    ),
    unit="1",
)

# === F-019: Per-session token budget (Redis-backed, fail-closed) ===
# Labels: reason (exhausted|store_unavailable) — bounded. The session id goes to
# the log line only, never to a label (F-028: no unbounded label values).
token_budget_exceeded_blocks = meter.create_counter(
    name="aigent.token_budget.exceeded",
    description=(
        "Requests refused because the session token budget was exhausted "
        "(or Redis was unavailable and the budget failed closed). "
        "Label: reason (exhausted|store_unavailable). Observable signal for the cap "
        "that was previously silent."
    ),
    unit="1",
)

# === Spec 46: Conversation history backend (fail-open) ===
# Labels: backend (redis|dynamodb|postgres), op (save|fetch|connect|purge) — both bounded.
history_backend_errors = meter.create_counter(
    name="aigent.history.backend_errors",
    description=(
        "Conversation-history store errors swallowed fail-open (the query still "
        "answers with empty/partial history). Labels: backend, op."
    ),
    unit="1",
)

# === Spec 47: User feedback ===
# Labels: agent_id (sanitised, bounded), score (positive|neutral|negative). Never
# user_id / session_id / response_id / consumer_id.
feedback_score = meter.create_counter(
    name="aigent.feedback.score",
    description=(
        "User ratings accepted by POST /feedback, by agent and score bucket "
        "(up/4/5=positive, 3=neutral, down/1/2=negative). Labels: agent_id, score."
    ),
    unit="1",
)
# Label: reason (unknown_response|already_rated|store_unavailable|record_failed).
feedback_errors = meter.create_counter(
    name="aigent.feedback.errors",
    description=(
        "Feedback refusals and fail-open recording errors. record_failed means a "
        "response id could not be stored (the query still answered). Label: reason."
    ),
    unit="1",
)

# === F-029: KB retention sweep + provenance (spec 21) ===
kb_retention_deleted = meter.create_counter(
    name="aigent.kb.retention.deleted",
    description="KB items deleted by the retention sweep, by rule (rejected_superseded | max_age)",
    unit="1",
)

kb_retention_errors = meter.create_counter(
    name="aigent.kb.retention.errors",
    description="KB retention sweeps that failed (fail-open: the KB keeps serving)",
    unit="1",
)

kb_provenance_written = meter.create_counter(
    name="aigent.kb.provenance.written",
    description="kb_provenance rows written alongside new KB items",
    unit="1",
)
