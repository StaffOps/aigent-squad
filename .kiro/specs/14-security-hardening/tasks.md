# Tasks: Security Hardening (anti-prompt-injection defense-in-depth)

Ordem por valor/risco. Cada camada é entregável independente — não big-bang.

## Phase 1 — Primary guardrail + fail-closed (maior ganho)
- [ ] Task 1: Terraform — provisionar Bedrock Guardrail (prompt-attack, denied topics, PII, multi-language) + output do guardrail id/version
- [ ] Task 2: `GuardrailClient` — aplicar guardrail no `bedrock.invoke` (input + output), via `guardrailIdentifier`/`guardrailVersion`
- [ ] Task 3: Fail-closed — guardrail bloqueia ou indisponível → 403 + audit log (NÃO bypass)
- [ ] Task 4: Audit log estruturado (detecção/recusa; sem payload em claro; com agent_id/user_id/session_id)
- [ ] Task 5: Tests ≥90% (block path, fail-closed path, allow path)

## Phase 2 — Exfiltration defense
- [ ] Task 6: `CanaryGuard` — injetar canary tokens no `infra_data`; detectar na saída
- [ ] Task 7: `OutputFilter` — scan de PII/segredos/canary na resposta antes de retornar
- [ ] Task 8: Tests ≥90% (canary leak → block; PII redaction)

## Phase 3 — Cost/abuse + input optimization
- [ ] Task 9: `RateLimiter` + `BudgetGuard` por usuário/sessão (Redis), fail-closed no budget
- [ ] Task 10: `InputScanner` — normalização (unicode/base64/homoglyph/zero-width) + heurísticas baratas pré-LLM (corta lixo antes do custo de invoke)
- [ ] Task 11: Tests ≥90%

## Phase 4 — Multi-language regression gate
- [ ] Task 12: Suíte de ataques em ≥5 idiomas (PT/EN/ES/zh/ar) + ofuscações (base64, leetspeak, zero-width, unicode confusables) — vira gate de CI
- [ ] Task 13: Reforçar context isolation (L3) com base no que a suíte revelar

## Phase 5 — Docs
- [ ] Task 14: `docs/SECURITY.md` — modelo de ameaça, camadas, fail-closed, postura competitiva; atualizar `READ_ONLY_POLICY.md` cruzando com esta spec

## Phase 1 status table (atualizar ao implementar)
| Task | Estado | Nota |
|------|--------|------|
| 1–14 | ❌ not started | spec escrita; implementação pendente |

## Promotion triggers (quando reabrir / endurecer)
- Falso-positivo do Guardrail inviabiliza uso → adicionar detector próprio como fallback L1.
- Requisito on-prem/multi-cloud → trocar Bedrock Guardrail por Llama Guard self-hosted.
- Read-only relaxado (agente passa a agir) → **reescrever a spec inteira** (elevation volta ao threat model; human-in-the-loop obrigatório).
