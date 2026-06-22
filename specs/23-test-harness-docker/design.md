# Design: Dockerized Test Harness

## Arquitetura

Um estágio de teste dockerizado, reusável dev↔CI, que roda `pytest` com cobertura e mocks — sem Python local, sem serviços reais.

```
requirements.txt + requirements-dev.txt ─▶ Dockerfile.test (python:3.11-slim, deps cacheadas)
                                                  │
                          docker run -v $PWD:/app ─┤
                                                  ▼
                           pytest --cov=src --cov-fail-under=90
                          (fakeredis · botocore stubber/moto · respx)
                                                  │
                                   exit≠0 se <90%  ▼  coverage.xml + term-missing
                                              dev local  ==  CI (spec 08)
```

## `Dockerfile.test`

```dockerfile
FROM python:3.11-slim
WORKDIR /app
# camada de deps cacheada (código muda mais que deps)
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt
# código entra via volume no `docker run` (dev) ou COPY (CI imutável)
CMD ["pytest", "--cov=src", "--cov-fail-under=90", \
     "--cov-report=term-missing", "--cov-report=xml"]
```

Comando único (dev):
```bash
docker build -f Dockerfile.test -t aigent-test .
docker run --rm -v "$PWD:/app" -w /app aigent-test
```

Ou sem build dedicado (ad-hoc, alinhado ao `dev-environment.md`):
```bash
docker run --rm -v "$PWD:/app" -w /app python:3.11-slim sh -c \
  "pip install -q -r requirements.txt -r requirements-dev.txt && \
   pytest --cov=src --cov-fail-under=90 --cov-report=term-missing"
```

## `requirements-dev.txt`

```
pytest
pytest-asyncio
pytest-cov
fakeredis        # Redis sem servidor
respx            # mock httpx (supervisor↔agente)
moto             # mock AWS (DynamoDB/Bedrock) — ou botocore Stubber
```

## `pyproject.toml` (trecho de teste)

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "--strict-markers"

[tool.coverage.run]
source = ["src"]
omit = ["*/tests/*"]
```

## Mocks por dependência (zero serviços reais)

| Dependência | Mock | Por quê |
|-------------|------|---------|
| Redis (`cache.py`) | `fakeredis` | testa fail-open (spec 06) sem subir Redis |
| DynamoDB (`state_store.py`) | `moto` / botocore `Stubber` | testa history + fail-open offline |
| Bedrock (`bedrock.py`) | botocore `Stubber` | testa retry/throttle/parse sem custo nem rede |
| HTTP agentes (`supervisor`) | `respx` | testa roteamento/fan-out/timeout sem subir agentes |

## Rationale (decisões e trade-offs)

### Decisão 1: Mesmo harness em dev e CI (não dois caminhos)

**Escolha**: o CI (spec 08) roda **exatamente** o mesmo Dockerfile/comando que o dev.

**Justificativa, em ordem de força**:
1. **Mata "passa local, falha no CI"** — a causa nº1 é divergência de ambiente; um único harness elimina isso (steering `error-recovery` → loop "funciona local/falha no target").
2. **Steering manda**: `dev-environment.md` exige builds/testes via Docker; um harness é a forma canônica.
3. **Paridade barata**: o mesmo `python:3.11-slim` em ambos; sem manter duas configs.

**Trade-offs aceitos**:
| Custo | Realidade |
|-------|-----------|
| Build da imagem de teste no 1º run | Camada de deps cacheada; runs seguintes são rápidos |

**Quando estaria errada**: se o CI precisar de matriz multi-versão (3.11 + 3.12) — aí o harness vira parametrizável; hoje o steering fixa 3.11.

### Decisão 2: Mocks (offline), não serviços reais no unit/contract

**Escolha**: unit/contract com `fakeredis`/`moto`/`respx`; integração com serviços reais fica fora de escopo (futuro).

**Justificativa**: testes offline são rápidos, determinísticos e baratos (o chaitops faz isso: 186 testes/~5s, sem serviços). Bedrock real custaria dinheiro e flakiness por throttle.

**Trade-off aceito**: mocks podem divergir do comportamento real → mitigado por *contract tests* (validar payloads reais, ex: Alertmanager v2) e por 1 smoke test dockerizado no CI da spec 08.

### Decisão 3: Reusar o padrão do `staffops-chaitops`

**Escolha**: copiar a abordagem do chaitops (`requirements-dev.txt` + `pytest --cov` em `python:3.11-slim`, fakeredis+respx) em vez de inventar.

**Justificativa**: já é provado naquele repo (ver `ECOSYSTEM.md`); reduz risco e mantém consistência no ecossistema.

## Invariantes

- Testes rodam **sem rede** e **sem serviços reais** (unit/contract).
- Cobertura **<90% = build falho** (exit≠0).
- `python:3.11-slim` (nunca 3.12 — steering).
- Dev e CI usam **o mesmo** harness.
- `requirements-dev.txt` **não** entra na imagem de produção.

## Dependências externas

| Lib | Uso |
|-----|-----|
| pytest, pytest-asyncio, pytest-cov | runner + async + cobertura |
| fakeredis, respx, moto/botocore Stubber | mocks offline |

## Verificação

O próprio harness é verificado rodando a suíte mínima das specs sem rede:
```bash
docker run --rm -v "$PWD:/app" -w /app aigent-test    # deve passar e reportar ≥90%
```
Confirmar: exit≠0 quando uma cobertura é forçada abaixo de 90% (teste do gate); zero conexões de rede durante a run.

## Riscos

- Drift mock↔real → mitigar com contract tests + smoke dockerizado no CI (spec 08).
- Imagem de teste inchada → `requirements-dev.txt` separado; multi-stage mantém prod enxuta.
- `asyncio_mode` mal configurado → testes async silenciosamente pulados; cobrir no review.
