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

    # Prompt caching (spec 11): add cache_control to system block.
    # Disable if the region/model rejects it (graceful degradation).
    bedrock_prompt_cache_enabled: bool = True

    # Token budget (spec 11): hard cap per session (total input+output tokens).
    # Default 200k — generous but prevents runaway sessions.
    session_token_budget: int = 200_000

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
    gateway_job_timeout_seconds: int = 45
    gateway_first_byte_timeout_seconds: int = 15
    gateway_idle_stream_timeout_seconds: int = 10
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
