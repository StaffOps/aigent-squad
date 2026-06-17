# Project Steering — AIgent-squad

Regras específicas deste projeto que estendem o steering global do StaffOps.

## Stack

| Camada | Tecnologia |
|--------|-----------|
| Language | Python 3.12 |
| Framework | FastAPI + uvicorn |
| LLM | AWS Bedrock (Anthropic Claude via `bedrock-runtime`) |
| State | DynamoDB (histórico, TTL 24h) — classe `ChatStorage` |
| Cache | Redis (dados de infra; **não** resposta de LLM) |
| Observability | OpenTelemetry (OTLP via env) + JSON logging |
| Deploy | Docker Compose (local) → EKS (alvo) |

## Arquitetura (invariantes)

- **1 supervisor + 5 especialistas** (aws, kubernetes, finops, devops, observability), cada um em seu pod/porta (8000–8005), MCP server em 8006.
- Roteamento é feito pelo **classifier** (Bedrock), nunca manual.
- Todo agente herda de `src/core/agent_base.py::Agent` e implementa `async process_request(...)`. **Padrão de referência: `src/agents/aws/`.**
- `server.py` é só transporte HTTP + instrumentação. **Zero lógica de negócio no server.**
- Contrato de resposta único: `{role, content, timestamp, agent_id}`.
- Histórico isolado por agente: DynamoDB `pk = user#session`, `sk = agent#timestamp`.

## Proibições (anti-patterns deste projeto)

- ❌ Redefinir a classe do agente dentro de `server.py` (use `agent.py`).
- ❌ `hash()` nativo em cache key (não-determinístico entre processos — use `hashlib.sha256`).
- ❌ Cachear a resposta do LLM por query (vaza entre usuários, quebra multiturno).
- ❌ Endpoint `/process` ou `/query` sem autenticação (`require_token`). `/health` é livre.
- ❌ Container rodando como root (use `USER 65534`).
- ❌ Hardcode de URL/endpoint que tem env var (`PROMETHEUS_URL`, `gitlab_url`, modelo Bedrock).
- ❌ Sugerir comandos de escrita/mutação — o sistema é **100% read-only** (4 camadas: prompt, IAM deny, RBAC, templates de recusa).
- ❌ `datetime.utcnow()` (deprecado) — use `datetime.now(timezone.utc)`.
- ❌ Reintroduzir LangGraph (foi removido; usa Bedrock direto).

## Read-only é lei

Todos os agentes são consultivos. Nunca executam `create/update/delete/terminate`. Diante de pedido de mudança: recusar e apontar para automação (Terraform/ArgoCD/GitOps). Ver `docs/READ_ONLY_POLICY.md`.

## Convenções

- Modelo Bedrock vem de **uma fonte única** (`config.py` → env `BEDROCK_MODEL_ID`). README, `.env.example` e compose devem concordar.
- Builds e testes **via Docker** (sem SDK local) — `python:3.11-slim` para testes (não 3.12, por pkg_resources/OTel).
- Spec-driven: mudança arquitetural atualiza `design.md` da spec correspondente **antes** de implementar.
- Versionamento por milestone validado, não por feature (ver steering global `version-management.md`). Não marcar "Production Ready" sem deploy real + testes.

## Ordem de trabalho atual (Fase 0)

`01-fix-blockers` → `02-unify-agent-architecture` → `03-fix-cache-observability` → `04-harden-security`.
Deploy (Fase 2): `05-helm-chart` (Helm chart para EKS).
Ver `.kiro/specs/ROADMAP.md` e `.kiro/specs/AUDIT.md`.
