from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
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
    bedrock_tier_deep_model_id: str = "us.anthropic.claude-opus-4-20250514-v1:0"

    # Tier routing control flags (spec 38 Phase 1).
    aigent_tier_routing_enabled: bool = True
    aigent_tier_deep_enabled: bool = False  # Phase 1: keep false until Opus access confirmed
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
        "You are a READ-ONLY assistant with live tools. FETCH data yourself with your tools and "
        "answer directly. Assume the user has NO kubectl/CLI/shell access — NEVER tell them to run "
        "kubectl / aws / helm commands (not even read-only ones like `kubectl get/logs/describe`).\n"
        "For visual exploration or write actions, point to the RIGHT dashboard and say WHICH and HOW:\n"
        "- DevOps dashboards live in the Grafana folder 'DevOps-GenericMonitoring' (subfolders: APM, "
        "BDCOtelHelper, Kubernetes, Synthetic Tests - Kuma). Recommend the specific dashboard "
        "(see the devops-grafana-dashboards skill) with its link.\n"
        "- Also: Grafana (metrics/logs/traces), ArgoCD (deploy/sync/rollback), Argo Workflows (jobs).\n"
        "If no dashboard, panel, or PromQL fits, OFFER to help build it (dashboard, PromQL, or the "
        "change) — never fall back to a shell command.\n"
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

    # DynamoDB
    dynamodb_sessions_table: str = "agent-sessions"
    dynamodb_endpoint: Optional[str] = None
    
    # ElastiCache
    redis_host: str
    redis_port: int = 6379
    redis_ssl: bool = True
    redis_password: Optional[str] = None
    
    # Slack (optional)
    slack_bot_token: Optional[str] = None
    slack_signing_secret: Optional[str] = None
    slack_proactive_channel: Optional[str] = None
    
    # API
    api_host: str = "0.0.0.0"  # nosec B104 — containerized service must bind all interfaces
    api_port: int = 8000

    # Edge gateway (spec 31) — the gateway fronts the supervisor.
    # SUPERVISOR_INTERNAL_TOKEN gates the supervisor's /internal/process; it is a
    # DISTINCT secret from INTERNAL_API_TOKEN (edge auth). Fail-closed: the
    # supervisor refuses /internal/process if this is unset (see internal_auth).
    supervisor_internal_token: Optional[str] = None
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

    # Admission guards (spec 31 L3 / spec 25 logic) — global, Redis-coordinated.
    # Account-wide limits (distinct from the per-replica worker-pool semaphore).
    # Fail-open: a Redis outage degrades to "allow" (availability over hard cap).
    rate_limit_per_minute: int = 60          # per-user sliding window
    daily_budget_usd: float = 50.0           # global daily Bedrock spend cap
    rate_budget_enabled: bool = True         # master switch for admission guards
    
    # GitLab (DevOps Agent)
    gitlab_token: Optional[str] = None
    gitlab_url: str = "https://gitlab.com"
    
    # MCP Servers
    mcp_servers: dict = {
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
    docs_portal_token: Optional[str] = None
    
    # RAG / Knowledge Base (FinOps)
    finops_knowledge_base_id: Optional[str] = None  # Bedrock Knowledge Base ID
    rag_enabled: bool = False  # Enable RAG retrieval

    
    class Config:
        env_file = ".env"
        case_sensitive = False

settings = Settings()
