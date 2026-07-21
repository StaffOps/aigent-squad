---
spec: 04-harden-security
status: done
completed: null
superseded_by: null
depends_on: []
deferred: []
---

# Feature: Harden Security

**Spec**: `04-harden-security`
**Severidade**: 🟠 High
**Achados**: S1, S2, S3, S4, S5 (ver `../AUDIT.md`)

Aplicar segurança por padrão aos serviços, alinhado a `cloud-security.md`, `k8s-best-practices.md` e `12-factor-app.md`. Hoje os endpoints são abertos, containers rodam como root e o Redis não tem auth.

## User Stories

WHEN um cliente chama qualquer endpoint `/process` ou `/query` THEN o serviço SHALL exigir autenticação (token compartilhado em dev; mTLS/NetworkPolicy em prod).

WHEN um container do squad sobe THEN ele SHALL rodar como usuário não-root com filesystem read-only e capabilities dropadas.

WHEN o sistema roda em produção THEN o acesso à AWS SHALL usar IRSA (não credenciais montadas), e o Redis SHALL ter auth + TLS.

WHEN dados não-confiáveis (input do usuário, saídas de GitLab/docs/inventory) entram no prompt THEN eles SHALL ser delimitados para reduzir prompt injection.

## Acceptance Criteria

- [ ] Endpoints internos exigem header de auth (`X-Internal-Token`) validado por env compartilhada; ausência → 401.
- [ ] MCP server e supervisor validam o token antes de rotear.
- [ ] Dockerfiles definem `USER` não-root; manifests/compose definem `securityContext` (runAsNonRoot, readOnlyRootFilesystem, drop ALL) onde aplicável.
- [ ] `docker-compose` documenta/define Redis com password; `REDIS_SSL` configurável.
- [ ] Documentado em `docs/` que prod usa IRSA + External Secrets (sem `~/.aws` montado).
- [ ] Prompts montam dados não-confiáveis dentro de delimitadores claros (ex.: blocos `<untrusted_data>`).
- [ ] NetworkPolicy de exemplo (ou nota no design) restringindo quem chama os agentes.

## Fora de escopo

- Implementar Istio Ambient/mTLS no cluster (depende de infra externa) — documentar como alvo de prod, entregar token compartilhado para dev/local.
- Kyverno policies (referência ao steering; não implementar aqui).
