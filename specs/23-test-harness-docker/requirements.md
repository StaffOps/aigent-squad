---
spec: 23-test-harness-docker
status: done
completed: 2026-06-18
superseded_by: null
depends_on: []
deferred: []
---

# Feature: Dockerized Test Harness

**Spec**: `23-test-harness-docker`
**Severidade**: 🔴 High (sem isso, não há como rodar/validar testes — máquina não tem SDK local)
**Origem**: requisito do usuário (melhorar os testes com Dockerfile); steering `dev-environment.md` (builds/testes só via Docker), `verification-independence.md` (gate ≥90%)
**Relação**: operacionaliza o gate de cobertura usado por TODAS as specs com código (06–22). A spec `08-ci-cd-pipeline` consome este harness no estágio de CI.

Estado atual ✅ verificado: **zero** testes, sem `pytest`/`tox`, sem deps de teste, sem CI. A máquina **não tem Python local** (steering). Logo, rodar teste = rodar em container. Esta spec define o **harness de teste dockerizado** reusável: um Dockerfile de teste (ou target multi-stage) + comando único que instala deps, roda `pytest` com cobertura e **falha abaixo de 90%**, sem serviços reais (mocks).

## User Stories

WHEN um dev roda os testes localmente THEN ele SHALL usar **um único comando Docker** que instala deps, executa `pytest` e reporta cobertura — sem instalar Python na máquina.

WHEN os testes rodam THEN dependências externas (Redis, DynamoDB, Bedrock, HTTP de agentes) SHALL ser **mockadas** (fakeredis, moto/stubber, respx) — **nenhum serviço real** necessário.

WHEN a cobertura fica **abaixo de 90%** THEN o comando SHALL retornar exit code ≠ 0 (gate de build).

WHEN o CI (spec 08) executa THEN ele SHALL reusar **o mesmo** harness/Dockerfile (paridade dev↔CI — sem divergência de ambiente).

WHEN a imagem de teste é construída THEN ela SHALL usar `python:3.11-slim` (não 3.12, por `pkg_resources`/OTel — steering) e cachear a camada de deps.

WHEN os testes terminam THEN o relatório de cobertura SHALL ser exportável (term + xml/html) para inspeção e para o CI.

## Acceptance Criteria

- [ ] `Dockerfile.test` (ou stage `test` no Dockerfile multi-stage) baseado em `python:3.11-slim`, com deps de runtime + `requirements-dev.txt` (pytest, pytest-asyncio, pytest-cov, fakeredis, respx, moto).
- [ ] Um comando único documentado roda tudo, ex:
  `docker run --rm -v "$PWD:/app" -w /app <img> sh -c "pytest --cov=src --cov-fail-under=90 --cov-report=term-missing --cov-report=xml"`.
- [ ] Camada de deps cacheada (copiar `requirements*.txt` antes do código).
- [ ] **Zero serviços reais**: Redis→fakeredis, DynamoDB/Bedrock→moto/botocore stubber, HTTP agentes→respx. Testes rodam offline.
- [ ] Gate `--cov-fail-under=90` (exit ≠ 0 abaixo disso) — alinha `verification-independence.md`.
- [ ] `pytest.ini`/`pyproject.toml` com config de testes (asyncio mode, paths, markers).
- [ ] `requirements-dev.txt` separado do runtime (não infla a imagem de produção).
- [ ] Relatório `coverage.xml` (para CI) + `term-missing` (para dev).
- [ ] CI (spec 08) reusa exatamente este harness (mesmo Dockerfile/command).
- [ ] README seção "Rodar testes" com o comando único.
- [ ] Testes (test-author ≠ autor): o próprio harness validado rodando a suíte mínima das specs (classifier, cache key, contrato, fail-open) sem rede.

## Fora de escopo

- Suíte de testes em si das features — cada spec (06–22) traz seus próprios testes; aqui é só o **harness**.
- Testes de integração com serviços reais (docker-compose de teste) — futuro; o MVP é unit/contract com mocks.
- Multi-arch da imagem de teste (amd64+arm64) — a de produção sim (spec 08); a de teste roda no arch do runner.
- Mutation testing / property-based — futuro.
