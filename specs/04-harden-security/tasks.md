# Tasks: Harden Security

- [x] T1: Criar `src/core/auth.py` com dependency `require_token` (X-Internal-Token) (S1) — done 2026-06-14
- [x] T2: Aplicar `Depends(require_token)` em `/process` (5 agentes) e `/query` (supervisor); `/health` livre (S1) — done 2026-06-14
- [x] T3: Supervisor injeta `X-Internal-Token` ao chamar agentes; MCP injeta ao chamar supervisor (S1) — done 2026-06-14
- [x] T4: Adicionar `USER` não-root nos 6 Dockerfiles + Dockerfile raiz (S2) — done 2026-06-14
- [x] T5: Adicionar `securityContext` no compose/manifests onde suportado (S2) — done 2026-06-14
- [x] T6: Redis com `--requirepass` no compose; propagar `REDIS_PASSWORD`/`REDIS_SSL` aos agentes (S3) — done 2026-06-14
- [x] T7: Delimitar dados não-confiáveis (`<user_query>`/`<infra_data>`) no contexto dos 5 agentes + reforço no prompt.md (S4) — done 2026-06-14
- [x] T8: Criar `docs/SECURITY.md` (modelo dev vs prod, IRSA, External Secrets, NetworkPolicy, mTLS) (S5) — done 2026-06-14
- [x] T9: Atualizar `.env.example` com `INTERNAL_API_TOKEN` e `REDIS_PASSWORD` (S1,S3) — done 2026-06-14
- [x] T10: Testes: 401 sem token / 200 com token; `id` non-root na imagem (depends on: T1,T2,T4) — done 2026-06-14

## Ordem sugerida
T1 → T2 → T3; T4/T5/T6 em paralelo; T7; T8/T9; T10 fecha.

## Notas
- Depende de spec 02 (servers unificados) para aplicar auth de forma consistente.
- Sinalizar ao usuário: criar serviços de rede sem authz é risco; esta spec é a que fecha isso.

## Status (2026-06-14)

**Completed**: All tasks (T1–T10). Auth middleware, non-root containers, Redis password, prompt injection delimiters, security documentation, .env.example updated.

**Production-only items documented as future work** (in `docs/SECURITY.md`):
- Istio mTLS (requires mesh deployment)
- NetworkPolicy (requires K8s deployment)
- External Secrets Operator (requires AWS infra)
- IRSA (requires EKS)

**Deferred**: Nothing code-side — spec fully complete for dev/local scope. Production hardening is documented for future deploy specs.
