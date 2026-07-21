---
name: root-cause-analysis
description: "Use when investigating production incidents, diagnosing failures, or performing RCA. Covers 5 Whys, fault tree, cross-signal correlation, timeline construction, empirical validation, and common failure patterns in K8s/cloud-native systems."
keywords: [root-cause-analysis, root, cause, analysis, "root cause", "cause analysis", rca]
---
# Root Cause Analysis

Técnicas e patterns para investigação de incidentes em sistemas distribuídos cloud-native.

---

## Técnicas de RCA

### 5 Whys (adaptado para sistemas distribuídos)

```
Sintoma: Service X retorna 500
  Why 1: Pod X está crashloopando
  Why 2: OOMKilled (excedeu memory limit)
  Why 3: Heap cresce indefinidamente após deploy Y
  Why 4: Deploy Y introduziu cache sem eviction
  Why 5: PR review não pegou ausência de TTL no cache → PROCESS GAP
  
Root Cause: Cache sem eviction policy (técnica)
Systemic Cause: Review checklist não inclui "memory behavior de cache" (processo)
```

Sempre buscar a **causa sistêmica** além da técnica — o que impediria recorrência.

### Fault Tree Analysis

```
                    [Service Down]
                   /              \
          [Pod crash]          [Network issue]
          /        \                  |
    [OOMKill]  [Liveness fail]  [DNS timeout]
        |           |                 |
  [Memory leak] [Deadlock]     [CoreDNS overload]
        |           |                 |
  [Cache no TTL] [Lock ordering]  [ndots:5 + large cluster]
```

Útil quando o sintoma pode ter múltiplas causas. Trabalhar cada branch até confirmar ou eliminar.

### Método da Eliminação

```
Possíveis causas: [A, B, C, D, E]

Teste 1: Se A fosse causa, veríamos X. X presente? → NÃO → eliminar A
Teste 2: Se B fosse causa, veríamos Y. Y presente? → SIM → B é candidato
Teste 3: Se C fosse causa, veríamos Z. Z presente? → NÃO → eliminar C
Teste 4: Se D fosse causa, veríamos W. W presente? → SIM → D é candidato
Teste 5: B e D podem coexistir? → NÃO → um refuta o outro → teste decisivo

Resultado: B confirmado, D refutado pelo teste 5
```

---

## Correlação Cross-Signal

### Matriz de sinais para problemas comuns

| Problema | Métrica | Log | Trace | Event | Deploy |
|----------|---------|-----|-------|-------|--------|
| Memory leak | `container_memory_working_set_bytes` crescente | OOMKill | Latência crescente (GC) | Pod restart | Sim (introduziu leak) |
| Connection leak | Connections open crescente, pool exhausted | "connection pool exhausted" | Timeout no DB call | — | Sim (mudou pool config) |
| DNS issue | Request duration spike | "lookup: i/o timeout" | Gaps entre spans | — | Não (infra) |
| Certificate expiry | — | "x509: certificate has expired" | TLS handshake fail | — | Não (cert rotation) |
| Resource starvation | CPU throttle, pending pods | — | — | FailedScheduling | Scaling event |
| Cascading failure | Multiple services error_rate up | Circuit breaker open | Cross-service error propagation | — | Single service deploy |

### Padrão de validação: 3 sinais concordam

```
VÁLIDO (3 sinais concordam):
  Métrica: error_rate up at 14:03 ✅
  Log: first error at 14:03:12 ✅  
  Deploy: rollout finished at 14:02:58 ✅
  → Forte correlação causal

INVÁLIDO (sinais discordam):
  Métrica: error_rate up at 14:03
  Log: first error at 13:45 (18 min ANTES!)
  Deploy: nenhum no período
  → Correlação com deploy REFUTADA — buscar outra causa
```

---

## Timeline Construction

### Fontes para construir timeline

| Fonte | Comando/Query | Granularidade |
|-------|---------------|---------------|
| K8s events | `kubectl get events --sort-by=.lastTimestamp` | segundo |
| Pod restarts | `kubectl get pods -o json \| jq '.items[].status.containerStatuses[].restartCount'` | — |
| ArgoCD syncs | ArgoCD UI / `argocd app history <app>` | minuto |
| Alertmanager | `/api/v2/alerts?active=true` | segundo |
| VictoriaMetrics | `changes(metric[5m])` para detectar step changes | 15s-1min |
| Loki | `{namespace="X"} \| level="error" \| first_over_time` | segundo |
| Git | `git log --since="2h ago" --oneline` | commit |

### Formato de timeline

```
[2026-06-01 14:00:00] BASELINE: all metrics normal
[2026-06-01 14:02:58] CHANGE: ArgoCD sync completed (app=service-x, image=v1.2.3→v1.2.4)
[2026-06-01 14:03:05] SIGNAL: first error log "connection refused" (pod service-x-abc)
[2026-06-01 14:03:12] SIGNAL: error_rate metric crosses threshold (0.1% → 12%)
[2026-06-01 14:03:30] SIGNAL: trace shows timeout on redis call (span_id=xyz)
[2026-06-01 14:04:00] ALERT: ErrorBudgetBurn fired (service=service-x)
[2026-06-01 14:05:00] ALERT: PodCrashLooping fired
[2026-06-01 14:10:00] ACTION: rollback initiated
[2026-06-01 14:11:30] RESOLVED: error_rate back to baseline after rollback
```

---

## Failure Patterns em K8s/Cloud-Native

### Pattern 1: Deploy → Crash

```
Sinal: CrashLoopBackOff após deploy
Investigar: OOMKill? Liveness fail? Startup crash?
  - OOM → verificar memory requests/limits vs uso real
  - Liveness → verificar timeout, path, startup delay
  - Crash → verificar logs do container (Previous: kubectl logs --previous)
```

### Pattern 2: Cascading failure

```
Sinal: Múltiplos serviços falhando simultaneamente
Investigar: Qual falhou PRIMEIRO? (timeline)
  - Upstream dependency (DB, cache, queue) degradou
  - Circuit breakers não configurados → thundering herd
  - Shared resource (node, network) saturou
```

### Pattern 3: Slow degradation

```
Sinal: Latência cresce linearmente ao longo de horas/dias
Investigar: Memory? Connections? Queue depth?
  - Memory leak (sem GC ou cache sem eviction)
  - Connection pool leak (connections abertas não devolvidas)
  - Queue backlog crescendo (consumer < producer rate)
```

### Pattern 4: Intermittent failures

```
Sinal: Erros esporádicos, não consistentes
Investigar: Scheduling? DNS? Certs? Specific nodes?
  - Problemas em nodes específicos (hardware, network)
  - DNS resolution flapping (CoreDNS saturation)
  - Certificate renewal window (valid on some pods, expired on others)
  - Race conditions (timing-dependent, hard to reproduce)
```

### Pattern 5: "Nothing changed" failures

```
Sinal: Falha sem deploy ou mudança visível
Investigar: O que mudou que NÃO é deploy?
  - Certificate expiry (automated rotation failed)
  - Secret rotation (External Secrets sync delay)
  - AWS service degradation (verify status page + CloudWatch)
  - Karpenter node rotation (new node, different config)
  - Spot interruption
  - DNS TTL expired + endpoint moved
  - Dependency SLA change (upstream rate limit hit)
```

---

## Validação Empírica

### Testes de confirmação

| Tipo | Como | Quando usar |
|------|------|-------------|
| **Rollback** | Reverter deploy, observar recuperação | Deploy-related issues |
| **Reprodução** | Triggerar a mesma condição deliberadamente | Bugs lógicos, race conditions |
| **Isolamento** | Desconectar componente suspeito, observar | Cascading failures |
| **Canary** | Aplicar fix em 1 pod, comparar com restante | Validar fix sem blast radius |
| **Contra-factual** | Comparar pod/node COM e SEM a condição | Environment-specific issues |

### Checklist antes de declarar "resolvido"

- [ ] Sintoma parou? (não só diminuiu)
- [ ] Métricas voltaram ao baseline?
- [ ] Nenhum alerta ativo relacionado?
- [ ] Fix faz sentido causal? (não coincidência)
- [ ] Monitorado por período adequado (≥15min para issues intermitentes)?
- [ ] Prevenção proposta? (alerta, teste, guardrail)
- [ ] Investigação documentada? (timeline + evidências + conclusão)
