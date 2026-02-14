from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # AWS
    aws_region: str = "us-east-1"
    bedrock_model_id: str = "anthropic.claude-sonnet-4-5-20250929-v1:0"  # Claude Sonnet 4.5 (latest)
    
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
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    
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
