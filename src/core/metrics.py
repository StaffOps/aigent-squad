"""Custom business metrics for AIgent-squad using otel-helper."""
from otel_helper import get_meter

meter = get_meter("aigent-squad")

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
