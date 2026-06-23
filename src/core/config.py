from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # AWS
    aws_region: str = "us-east-1"
    bedrock_model_id: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"  # Claude Sonnet 4.5 (US inference profile; model requires INFERENCE_PROFILE, not on-demand)

    # Bedrock Guardrail — anti-prompt-injection (spec 14, L1). Fail-closed:
    # when guardrail_enabled and no id is configured, invoke is refused (not
    # bypassed). Provision via infra/terraform/guardrail (outputs id/version).
    guardrail_enabled: bool = True
    guardrail_id: Optional[str] = None
    guardrail_version: str = "DRAFT"
    
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
