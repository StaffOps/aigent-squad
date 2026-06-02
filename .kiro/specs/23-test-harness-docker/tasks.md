# Tasks: Dockerized Test Harness

> Pré-requisito de verificação de TODAS as specs com código (06–22). A spec 08 (CI/CD) reusa este harness. Reusa o padrão do `staffops-chaitops`.

- [ ] T1: `requirements-dev.txt` (pytest, pytest-asyncio, pytest-cov, fakeredis, respx, moto) — separado do runtime
- [ ] T2: `Dockerfile.test` (`python:3.11-slim`, deps cacheadas: COPY requirements*.txt antes do código) (depends on: T1)
- [ ] T3: Config de teste em `pyproject.toml` (`asyncio_mode=auto`, `testpaths`, `[tool.coverage.run] source=src`)
- [ ] T4: Comando único documentado + gate `--cov-fail-under=90` (`--cov-report=term-missing,xml`) (depends on: T2, T3)
- [ ] T5: Fixtures base de mock em `tests/conftest.py` — fakeredis, botocore Stubber (Bedrock/DynamoDB), respx (HTTP agentes) (depends on: T1)
- [ ] T6: Suíte mínima offline (classifier parsing, cache key sha256, contrato de resposta, fail-open Redis/DynamoDB) provando o harness (depends on: T5)
- [ ] T7: Verificar o gate — forçar cobertura <90% e confirmar exit≠0; confirmar run sem rede (depends on: T4, T6)
- [ ] T8 (test-author DIFERENTE do autor): revisar/expandir a suíte mínima contra contrato (não espelhar implementação) (depends on: T6)
- [ ] T9: Review independente (`code-review`): mocks corretos, zero rede, gate efetivo, paridade dev↔CI (depends on: T8)
- [ ] T10: README seção "Rodar testes" (comando único) + nota de paridade com CI (spec 08) (depends on: T4)

## Ordem sugerida
T1→T2; T3; T4; T5→T6→T7; T8→T9→T10.

## Notas
- Reusar a abordagem do chaitops (fakeredis+respx, `pytest --cov`, 3.11-slim) — não inventar.
- O CI (spec 08) DEVE chamar o mesmo Dockerfile/comando — sem segundo caminho.
- Mocks offline; integração com serviços reais é fora de escopo (futuro).
- Pipeline de verificação (`verification-independence.md`): T1–T7/T10 autor; T8 test-author em sessão diferente; T9 code-review.
- Per `documentation-sync`: README ganha a seção na mesma mudança.
