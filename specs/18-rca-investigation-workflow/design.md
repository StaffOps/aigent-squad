# Design: RCA Investigation Workflow

## Arquitetura

Uma camada de **orquestração de investigação** sobre o fan-out da spec 17. O supervisor ganha um modo "investigate" que segue um ciclo determinístico:

```
Sintoma → [decisão: trivial? → fast-path]
            │ não-trivial
            ▼
   1. Planejar janela temporal + agentes relevantes (config-driven)
   2. Fan-out PARALELO de coleta de evidência (spec 17)
        ├─ observability: métricas/logs/traces na janela
        ├─ kubernetes:    events, restarts, OOMKills
        ├─ devops:        deploys/MRs no período
        └─ aws:           saúde de infra (RDS/cache/nodes)
   3. Normalizar → Evidence[] (com strength)
   4. Construir Timeline (ordenada, causa antes do efeito)
   5. Correlacionar (≥3 sinais independentes → hipótese forte)
   6. Sintetizar RCA (1 Bedrock call) + propor prevenção
```

## Triggers de investigação

A investigação pode ser disparada por **duas fontes** (decisão 2026-06-02):

| Trigger | Endpoint | Payload |
|---------|----------|---------|
| **Usuário** (chat) | `POST /chat` com `mode=investigate` ou detecção automática de sintoma | Texto livre |
| **Alertmanager** (webhook) | `POST /alerts/incoming` | Alertmanager v2 JSON (alerts[], groupLabels, commonLabels) |

O endpoint `/alerts/incoming` vive no **supervisor** (que já é FastAPI). Não requer serviço novo. O alerta é convertido em sintoma estruturado (severity, service, timestamp, labels) e entra no mesmo ciclo de investigação.

## Rodadas e scratchpad

**max_rounds** (config-driven, default=5): número máximo de rodadas de coleta por investigação. A cada rodada, o synthesizer avalia se a evidência é suficiente ou precisa de mais. O teto é hard stop (não recomendação).

| Nível (ROADMAP) | max_rounds | Contexto entre rodadas |
|-----------------|------------|------------------------|
| 1 (MVP) | 1 (fixa) | — |
| 2 (iterativo) | 5 | Synthesizer identifica gaps → 2ª+ rodada direcionada |
| 3 (compartilhado) | 10 | Agentes recebem resumo dos outros via scratchpad |
| 4 (autônomo) | 25 | Agentes propõem hipóteses + delegam |

**Scratchpad** (memória curta por investigação):
- Dict/Redis hash keyed por `investigation_id`
- Contém: evidência coletada até agora, hipóteses correntes, gaps identificados
- Lifetime: duração da investigação (deletado ao finalizar)
- A partir do Nível 3, o scratchpad é injetado no prompt dos agentes na rodada seguinte

```python
@dataclass
class InvestigationState:
    id: str
    symptom: str
    rounds_completed: int
    max_rounds: int
    evidence: list[Evidence]
    hypotheses: list[str]
    gaps: list[str]  # "o que falta coletar"
```

## Componentes

| Componente | Responsabilidade | Onde |
|-----------|------------------|------|
| Investigation orchestrator | Ciclo acima; decide trivial vs investigar; controla rodadas | `src/supervisor/investigation.py` (novo) |
| Alert ingestion | Converte payload Alertmanager → sintoma estruturado | `src/supervisor/alert_handler.py` (novo) |
| Evidence model | Estrutura normalizada de evidência | `src/core/investigation.py` (novo) |
| Timeline builder | Ordena eventos, marca candidatos a causa | `src/core/investigation.py` |
| Correlator | Regra de confiança por nº de sinais independentes | `src/core/investigation.py` |
| RCA synthesizer | Funde evidência → RCA + prevenção; detecta gaps pra próxima rodada | `src/supervisor/investigation.py` |
| Scratchpad | Estado por investigação (evidência, hipóteses, gaps) | `src/core/investigation.py` (in-memory ou Redis) |

> Agentes **não mudam de responsabilidade**: continuam consultivos e read-only. O que muda é o supervisor passar uma *intenção de coleta de evidência* (janela + foco) em vez de uma pergunta livre.

## Modelos (contrato)

```python
@dataclass
class Evidence:
    source_agent: str
    signal_type: str        # metric | log | trace | event | deploy | infra
    timestamp: str          # ISO; "" se não-temporal
    strength: str           # forte | media | fraca  (ver hierarquia abaixo)
    summary: str

@dataclass
class RCAResult:
    hypothesis: str
    confidence: str         # alta | media | baixa
    evidence: list[Evidence]
    timeline: list[Evidence]      # subconjunto temporal, ordenado
    contradicting: list[Evidence] # contra-evidência (não descartar)
    prevention: list[str]         # alerta/teste/guardrail/runbook
```

### Modelo de evidência e correlação → ver `../EVIDENCE-MODEL.md`

O catálogo completo de sinais (33 signals C1-C8/M1-M13/I1-I8/T1-T4/E1-E4), as 14 assinaturas de root-cause, o algoritmo de confiança e o teste de independência vivem em **`specs/EVIDENCE-MODEL.md`** (deliberação observability+sre+troubleshoot, 2026-06-02). Resumo do que muda aqui:

**Correlação por CAMADAS causais, não por contagem de sinais:**
```
CHANGE (o que mudou) + MECHANISM (como causou) + IMPACT (dano) + ordem temporal válida
  + 0 contra-evidência não-explicada  →  confiança ALTA
```
- **Track A** (event-driven): CHANGE+MECHANISM+IMPACT.
- **Track B** (degradação sem mudança explícita): MECHANISM contínuo (ex: memory growth monotônico) + MECHANISM confirmante + IMPACT + sem CHANGE alternativo.

**Independência real** (resolve o "≥3 sinais" ingênuo): dois sinais derivados um do outro (OOMKill→restart, error_rate↔error_log) contam como UM. Exemplar metric→trace→log do mesmo request é profundidade (qualidade da prova), não largura (contagem). Sinais da mesma camada+mesmo fault_domain são dependentes por default.

**Timestamps**: CloudWatch atrasa até 120s — nunca ancora ordem causal. Métricas (15s) não resolvem cascata sub-15s — usar Loki `direction=forward&limit=1` por serviço. Tolerâncias de timing por par de fontes em `EVIDENCE-MODEL.md §5`.

**Confiança do LLM**: teto (synthesizer nunca aumenta) + piso flexível (pode baixar com justificativa logada).

## Decisão trivial vs investigar

Limiar explícito (barato, sem Bedrock extra): se o sintoma casa com um padrão de domínio único e fix conhecido (ex: "typo no YAML", "como configuro X") → fast-path (1 agente). Caso contrário, ou se o usuário usa `mode=investigate`, abre investigação. Segue o `investigation-protocol` ("causa óbvia em <30s? → fix direto").

## Rationale (decisões e trade-offs)

### Decisão 1: RCA como orquestração sobre fan-out (não um "agente RCA" novo)

**Escolha**: a investigação é um **modo do supervisor** que reusa os 5 agentes existentes como coletores de evidência; não criamos um 6º agente "troubleshooter".

**Justificativa, em ordem de força**:
1. **Os agentes já têm acesso aos sinais** (observability→métricas, k8s→events, devops→deploys). Um agente RCA novo duplicaria esses acessos e a lógica de cada datasource.
2. **Simplicidade de produto**: menos componentes pra operar/configurar. O usuário pediu "não complexo demais".
3. **Reuso direto** do fan-out (17) e do config (19) — a investigação é uma *composição*, não uma nova peça.

**Trade-offs aceitos**:
| Custo | Realidade |
|-------|-----------|
| O supervisor fica mais "gordo" (ganha a camada de investigação) | É coesão, não acoplamento — a lógica de correlação não pertence a nenhum agente de domínio |
| A correlação é centralizada | Correto: só o supervisor vê todos os sinais; correlação distribuída precisaria de estado compartilhado |

**Quando estaria errada** (signals pra reabrir): se a lógica de investigação crescer a ponto de ter seu próprio ciclo de vida/escala — aí vira serviço próprio.

### Decisão 2: 1 rodada de coleta (não loop investigativo iterativo) na Fase 1

**Escolha**: Fase 1 faz **uma** rodada de fan-out → correlação → RCA. Não há "coletar mais evidência baseado na 1ª hipótese".

**Justificativa**:
1. **Latência e custo previsíveis** — investigação iterativa multiplica chamadas Bedrock sem teto claro.
2. **Cobre a maioria** dos casos de RCA cross-domain com 1 boa rodada paralela.
3. **Evita complexidade** prematura antes do produto provar valor.

**Trade-off aceito**: casos que exigem aprofundamento ("a 1ª rodada sugere memory leak → agora preciso do heap profile") ficam pra Fase 2. Marcado em `tasks.md` com promotion trigger.

**Quando estaria errada**: se na prática a maioria das RCAs precisar de 2+ rodadas pra concluir.

### Decisão 3: Read-only absoluto — propõe prevenção, nunca remedia

**Escolha**: a investigação termina em **proposta** de prevenção; jamais executa fix.

**Justificativa**: invariante read-only do projeto (4 camadas) + segurança. Um RCA errado executando remediação automática é o pior cenário possível num produto.

**Trade-off aceito**: o usuário ainda aplica o fix manualmente — aceitável e desejável num produto consultivo.

## Invariantes

- Investigação é **read-only** (só lê sinais; prevenção é texto, não ação).
- Sintoma trivial **não** abre investigação (fast-path).
- Contra-evidência sempre registrada e afeta a confiança.
- Teto de custo (nº agentes, nº queries de evidência) aplicado **antes** das chamadas (config — spec 19).
- Confiança nunca "alta" com <3 sinais independentes.

## Dependências externas

| Serviço | Via agente |
|---------|-----------|
| VictoriaMetrics/Prometheus, Loki, Tempo | observability |
| Kubernetes API (events) | kubernetes |
| GitLab (deploys/MRs) | devops |
| CloudWatch/infra | aws |
| Bedrock (síntese RCA, Sonnet) | supervisor |

Datasources e janelas default vêm do config (spec 19), não hardcoded.

## Verificação

```bash
docker run --rm -v $(pwd):/app -w /app python:3.11-slim sh -c \
  "pip install -q -r requirements.txt pytest pytest-asyncio && pytest tests/ -v --cov=src --cov-fail-under=90"
```

Testes-chave (test-author ≠ autor): coleta paralela com agentes mockados; timeline ordena por timestamp e marca o deploy como candidato; correlação devolve "alta" com 3 sinais e "baixa" com 1; contra-evidência rebaixa confiança; sintoma trivial não chama fan-out.

## Riscos

- **Custo**: investigação = vários agentes + síntese. Mitigado por teto config + fast-path + Haiku no classifier (spec 11).
- **Falsa confiança**: correlação temporal ≠ causalidade. Mitigado pela hierarquia de força + exigência de ≥3 sinais + registro de contra-evidência.
- **Qualidade dos timestamps**: sem timestamps precisos não há timeline. Depende da qualidade dos sinais que os agentes retornam (spec 09 ajuda).
