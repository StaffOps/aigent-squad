from pydantic import BeforeValidator, Field, SecretStr
from pydantic_settings import BaseSettings
from typing import Annotated, Any, Literal, Optional, Union

def reveal(value: Union[SecretStr, str, None]) -> str:
    """Plaintext of a secret setting ('' when unset) — call ONLY at the use site.

    Secret settings are ``SecretStr`` (B-32) so ``repr(settings)``/debug dumps show
    ``'**********'``. A plain ``str`` is accepted too (tests assign one directly).
    """
    if value is None:
        return ""
    if isinstance(value, SecretStr):
        return str(value.get_secret_value())
    # Coerce anything else (e.g. a MagicMock in tests) so callers always get a str:
    # a comparison then fails closed instead of raising TypeError.
    return str(value)


def _empty_is_unset(value: Any) -> Any:
    """Blank string setting -> None (a chart renders an unset value as "")."""
    if isinstance(value, str) and not value.strip():
        return None
    return value


class Settings(BaseSettings):  # type: ignore[misc]
    # LLM provider (spec 28) — GLOBAL selection. "bedrock" (default) keeps the
    # historical behavior byte-identical; "openai" routes every LLM call to an
    # OpenAI-compatible POST {OPENAI_BASE_URL}/chat/completions. Under "openai"
    # the Bedrock Guardrail (L1) is switched off automatically — a documented
    # exception to fail-closed security (AGENTS.md invariant 6, ADR-0009).
    llm_provider: Literal["bedrock", "openai"] = "bedrock"
    # Provider-neutral model ids per role. Unset → fall back to the BEDROCK_*
    # role vars (bedrock) or to LLM_MODEL_ID (openai; LLM_MODEL_ID is required).
    llm_model_id: Optional[str] = None
    llm_classifier_model_id: Optional[str] = None
    llm_synthesis_model_id: Optional[str] = None
    # Per-1M-token USD pricing for non-Bedrock models (spec 28 Decision 9).
    # Both unset → cost metrics read 0 (startup WARNING); never invented.
    llm_price_input_per_1m: Optional[float] = None
    llm_price_output_per_1m: Optional[float] = None

    # OpenAI-compatible endpoint (LLM_PROVIDER=openai). The key is a secret:
    # prefer OPENAI_API_KEY_FILE (file-mounted); it wins over OPENAI_API_KEY.
    openai_base_url: Optional[str] = None
    openai_api_key: Optional[SecretStr] = None
    openai_api_key_file: Optional[str] = None
    # "Authorization" sends "Bearer <key>"; any other header sends the raw key
    # (e.g. "api-key" for Azure OpenAI-compatible endpoints).
    openai_api_key_header: str = "Authorization"
    # Request field carrying the output-token cap ("max_completion_tokens" for
    # OpenAI reasoning models; most compatible servers accept "max_tokens").
    openai_max_tokens_field: str = "max_tokens"

    # AWS
    aws_region: str = "us-east-1"
    bedrock_model_id: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"  # Claude Sonnet 4.5 (US inference profile; model requires INFERENCE_PROFILE, not on-demand)

    # Model tiering (spec 11): role → model ID.  Override via env vars
    # BEDROCK_CLASSIFIER_MODEL_ID, BEDROCK_AGENT_MODEL_ID, BEDROCK_SYNTHESIS_MODEL_ID.
    bedrock_classifier_model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    bedrock_agent_model_id: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    bedrock_synthesis_model_id: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

    # Model-tier PRE-ROUTING (spec 38): complexity-aware tier → model ID.
    # Override via env BEDROCK_TIER_FAST_MODEL_ID / BEDROCK_TIER_STANDARD_MODEL_ID /
    # BEDROCK_TIER_DEEP_MODEL_ID.
    bedrock_tier_fast_model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    bedrock_tier_standard_model_id: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    bedrock_tier_deep_model_id: str = "us.anthropic.claude-opus-4-5-20251101-v1:0"  # Opus 4.5 (verified ACTIVE in-account 2026-07-23; the older Opus 4.0 profile no longer exists here)

    # Tier routing control flags (spec 38 Phase 1).
    aigent_tier_routing_enabled: bool = True
    aigent_tier_deep_enabled: bool = False  # code default false; enable per-env via overlay (Opus 4.5 access CONFIRMED 2026-07-23)
    aigent_tier_confidence_high: float = 0.85

    # Shared always-on agent instructions — appended to EVERY agent's system prompt.
    # Env-overridable (CALIBRATED_HONESTY_INSTRUCTION / SELF_SERVICE_INSTRUCTION) so the
    # policy text is tunable via Helm values WITHOUT a rebuild.
    calibrated_honesty_instruction: str = (
        "<calibrated_honesty>\n"
        "Separate VERIFIED facts (backed by a tool result or datasource response THIS turn) "
        "from INFERRED/assumed statements — label inferences explicitly.\n"
        "NEVER state a metric value, resource state, or count you did not retrieve this turn. "
        "If you didn't verify it, say 'não consegui confirmar' / 'I could not verify'.\n"
        "End every answer with one short line: confidence level (alta/média/baixa or high/medium/low) "
        "AND a brief list of claims you could NOT verify this turn "
        "(or 'nada não-verificado' / 'nothing unverified').\n"
        "</calibrated_honesty>"
    )
    self_service_instruction: str = (
        "<self_service>\n"
        "You have live read-only tools — fetch the data yourself and answer directly. The user may "
        "not have CLI access, so lead with the answer from your tools rather than handing them "
        "kubectl/aws/helm commands to run. When something needs a UI, or an action you can't take, "
        "point to the right place and say which and how:\n"
        "- The DevOps monitoring dashboards live in Grafana — recommend the specific dashboard "
        "(see the devops-grafana-dashboards skill for the exact folders and UIDs) with its link.\n"
        "- Also: Grafana (metrics/logs/traces), ArgoCD (deploy/sync/rollback), Argo Workflows (jobs).\n"
        "If nothing quite fits, offer to help build it (a dashboard, a PromQL query, or the change).\n"
        "</self_service>"
    )
    decisiveness_instruction: str = (
        "<decisiveness>\n"
        "Be decisive with tools. Do ONE discovery pass (list metrics/labels/resources) THEN run "
        "TARGETED queries — do NOT exhaustively enumerate or re-query the same thing with minor "
        "variations. Prefer a few high-value tool calls over many. Batch independent lookups in a "
        "single turn when possible. As soon as you have enough to answer, STOP and answer — do not "
        "keep gathering 'for completeness'.\n"
        "</decisiveness>"
    )
    # Grafana root URL for clickable DevOps-GenericMonitoring dashboard links.
    # Default empty (scrub-clean); real value injected via GRAFANA_BASE_URL in the
    # k8s-setup overlay (internal infra config), not hardcoded in this repo.
    grafana_base_url: str = ""

    # Prompt caching (spec 11): add cache_control to system block.
    # Disable if the region/model rejects it (graceful degradation).
    bedrock_prompt_cache_enabled: bool = True

    # Token budget (spec 11): hard cap per session (total input+output tokens).
    # Default 200k — generous but prevents runaway sessions.
    session_token_budget: int = 2_000_000

    # History truncation by tokens (spec 11). Controls how many tokens of chat
    # history are included in each Bedrock call (not session-wide budget).
    history_max_tokens: int = 8_000

    # Bedrock Guardrail — anti-prompt-injection (spec 14, L1). Fail-closed:
    # when guardrail_enabled and no id is configured, invoke is refused (not
    # bypassed). Provision via infra/terraform/guardrail (outputs id/version).
    guardrail_enabled: bool = True
    guardrail_id: Optional[str] = None
    guardrail_version: str = "DRAFT"

    # Canary Guard — exfiltration detection (spec 14, L5). Injects per-request
    # tokens into infra_data; if they appear in the output, redacts them and
    # returns the response (redact-and-continue since F-005, 2026-07-13 — a
    # deliberate exception to the other layers' fail-closed default; see
    # src/core/canary.py module docstring).
    canary_enabled: bool = True
    # Repeated detections within the SAME session escalate from redact to
    # hard-block (independent review 2026-07-14, F-005 follow-up): the
    # redact-and-continue policy is a soft oracle an attacker could probe
    # for exfiltration/obfuscation techniques (each detection tells them
    # their payload survived to the model's output, without ever hard-
    # failing). A one-off benign false positive essentially never repeats
    # this many times in one session; sustained detections are a real
    # signal, not noise, so escalate to fail-closed.
    canary_escalation_threshold: int = 3

    # Output Filter — PII/secret leak detection (spec 14, L4). Scans model
    # response for credentials, PII, keys before returning. Fail-closed.
    output_filter_enabled: bool = True

    # Response Quality Guard — structural defect detection (spec 35, T1).
    # Scans model response for tool-call scaffolding leaks and raw adapter/
    # infra error text reaching the user verbatim (F-001/F-002/F-003 classes).
    # Fail-closed — unlike canary, neither defect class is ever legitimate.
    response_quality_enabled: bool = True

    # Input Scanner — pre-LLM normalization + cheap heuristics (spec 14, L2).
    # Normalizes unicode/homoglyphs, rejects junk before spending a Bedrock
    # invoke. Fail-closed on scanner error. Runs before context construction.
    input_scanner_enabled: bool = True

    # Conversation history backend (spec 46). `redis` (default) works in every
    # environment because Redis is already required; the AWS deployment sets
    # HISTORY_BACKEND=dynamodb explicitly; `postgres` is opt-in (durable/auditable,
    # plain table, no pgvector). An invalid value fails startup (Literal).
    history_backend: Literal["redis", "dynamodb", "postgres"] = "redis"
    # Per-message cap on stored history `content` (every backend). Bounds memory
    # (Redis shares it with rate/budget counters) and row size; longer content is
    # cut and ends with a truncation marker. 0 disables the cap.
    history_max_message_chars: int = Field(default=16_000, ge=0)

    # DynamoDB
    dynamodb_sessions_table: str = "agent-sessions"
    dynamodb_endpoint: Optional[str] = None
    # Local dev only (docker-compose): sign DynamoDB-local requests with fixed dummy
    # credentials instead of the AWS chain, so an expired SSO token (needed only for
    # Bedrock) does not take conversation history — and the supervisor's /ready —
    # down with it. Ignored unless DYNAMODB_ENDPOINT is set. Never enable in a cluster.
    dynamodb_local_static_credentials: bool = False
    
    # ElastiCache
    redis_host: str
    redis_port: int = 6379
    redis_ssl: bool = True
    redis_password: Optional[SecretStr] = None
    
    # Slack (optional)
    slack_bot_token: Optional[SecretStr] = None
    slack_signing_secret: Optional[SecretStr] = None
    slack_proactive_channel: Optional[str] = None
    
    # API
    api_host: str = "0.0.0.0"  # nosec B104 — containerized service must bind all interfaces
    api_port: int = 8000

    # Edge gateway (spec 31) — the gateway fronts the supervisor.
    # SUPERVISOR_INTERNAL_TOKEN gates the supervisor's /internal/process; it is a
    # DISTINCT secret from INTERNAL_API_TOKEN (edge auth). Fail-closed: the
    # supervisor refuses /internal/process if this is unset (see internal_auth).
    supervisor_internal_token: Optional[SecretStr] = None
    # Where the gateway forwards to (supervisor Service URL in K8s).
    supervisor_url: str = "http://localhost:8001"
    # Worker pool (gateway-side admission). Defaults from spec 31 round-table.
    gateway_max_concurrent: int = 20
    gateway_job_timeout_seconds: int = 150
    gateway_first_byte_timeout_seconds: int = 90
    gateway_idle_stream_timeout_seconds: int = 35
    # Bedrock boto3 client timeouts. Default read_timeout (60s) was cut on slow Converse
    # turns (large context + Sonnet) → ReadTimeoutError → stream 'terminated'. Keep below
    # gateway_job_timeout_seconds so the gateway doesn't cut first.
    bedrock_read_timeout_seconds: int = 120
    bedrock_connect_timeout_seconds: int = 10
    gateway_cancel_poll_seconds: float = 0.5
    # TTL of the gateway's ``job:<id>`` status records in Redis (F-028). Status is
    # informational only; without an expiry the records accumulated forever.
    gateway_job_record_ttl_seconds: int = Field(default=86400, gt=0)
    # Bedrock concurrency semaphore (spec 25 T8): max in-flight Bedrock calls.
    # Prevents overwhelming the Bedrock endpoint (ThrottlingException cascade).
    bedrock_max_concurrent: int = 20

    # Admission guards (spec 31 L3 / spec 25 logic) — global, Redis-coordinated.
    # Account-wide limits (distinct from the per-replica worker-pool semaphore).
    # Fail-open: a Redis outage degrades to "allow" (availability over hard cap).
    rate_limit_per_minute: int = 60          # per-user sliding window
    daily_budget_usd: float = 50.0           # global daily Bedrock spend cap
    rate_budget_enabled: bool = True         # master switch for admission guards
    
    # GitLab (DevOps Agent)
    gitlab_token: Optional[SecretStr] = None
    gitlab_url: str = "https://gitlab.com"
    
    # MCP Servers
    mcp_servers: dict[str, str] = {
        "aws-mcp": "http://aws-mcp-server.default.svc.cluster.local:8080",
        "k8s-mcp": "http://k8s-mcp-server.default.svc.cluster.local:8080",
    }
    
    # Kubecost Athena (FinOps)
    athena_project_id: str = "123456789012"
    athena_bucket: str = "s3://company-athena-kubecost"
    athena_region: str = "us-east-1"
    athena_database: str = "kubecost"
    athena_table: str = "kubecost_split"
    athena_workgroup: str = "primary"
    
    # Documentation Portal (DevOps)
    docs_portal_url: str = "https://docs.company.internal/"
    docs_portal_url_new: str = "https://docs.company.com/"
    docs_portal_token: Optional[SecretStr] = None
    
    # Incident-memory KB store (spec 21, PostgreSQL + pgvector; optional). Same
    # defaults the store used via os.getenv before B-32 — the password default
    # matches docker-compose's local-only "changeme".
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_user: str = "aigent"
    postgres_password: Optional[SecretStr] = SecretStr("changeme")
    postgres_db: str = "aigent_kb"
    # TLS mode for the Postgres HISTORY store (spec 46), passed to asyncpg as
    # `ssl=`. Unset (or empty) = asyncpg's default. The KB store is unaffected.
    # Charts often render an unset value as "" — that also means "not set".
    postgres_sslmode: Annotated[
        Optional[Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]],
        BeforeValidator(_empty_is_unset),
    ] = None
    # CA bundle (PEM path) that verifies the server certificate when
    # POSTGRES_SSLMODE is verify-ca / verify-full. `require` encrypts WITHOUT
    # verifying; verify-* need a CA (e.g. the cloud provider's RDS bundle mounted
    # in the pod). Unset falls back to PGSSLROOTCERT, then ~/.postgresql/root.crt.
    postgres_sslrootcert: Annotated[Optional[str], BeforeValidator(_empty_is_unset)] = None

    # Incident-memory KB switch + retention (spec 21, F-029). KB_ENABLED=false
    # turns the KB off completely: no distillation, no RAG, no store connection,
    # and the /kb/* endpoints answer 503. Default true keeps the old behaviour
    # (the KB runs whenever its Postgres is reachable).
    kb_enabled: bool = True
    # Rejected and superseded items are deleted this many days after their last
    # status change (updated_at).
    kb_rejected_retention_days: int = Field(default=30, gt=0)
    # Optional maximum age (days since created_at) for every other item (active
    # and pending review). Unset = keep them until someone deletes them.
    kb_item_max_age_days: Annotated[Optional[Annotated[int, Field(gt=0)]], BeforeValidator(_empty_is_unset)] = None
    # The retention sweep runs at most once per interval, opportunistically (at
    # connect and on KB reads/writes — no cron). Each sweep deletes at most
    # batch_size x max_batches rows per rule.
    kb_retention_interval_seconds: int = Field(default=3600, gt=0)
    kb_retention_batch_size: int = Field(default=500, gt=0)
    kb_retention_max_batches: int = Field(default=10, gt=0)

    # RAG / Knowledge Base (FinOps)
    finops_knowledge_base_id: Optional[str] = None  # Bedrock Knowledge Base ID
    rag_enabled: bool = False  # Enable RAG retrieval

    
    class Config:
        env_file = ".env"
        case_sensitive = False

settings = Settings()
