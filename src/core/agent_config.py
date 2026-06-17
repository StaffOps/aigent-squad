"""Agent configuration schema — defines what an agent IS via YAML."""
from pydantic import BaseModel, Field
from typing import Optional


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
