---
name: otel-ebpf-instrumentation
description: OpenTelemetry eBPF Instrumentation (OBI) configuration at <org>. Use when configuring auto-instrumentation for apps without SDK, network metrics, context propagation, service discovery, or tuning eBPF performance. Covers DaemonSet deployment, discovery by namespace, network inter-zone (FinOps), context propagation, routes/filters, and cardinality control.
keywords: [otel-ebpf-instrumentation, otel, ebpf, instrumentation, "otel ebpf", "ebpf instrumentation", opentelemetry, obi, sdk]
---
# OTel eBPF Instrumentation (OBI)

Auto-instrumentação via eBPF para apps sem SDK OTel. Gera traces e métricas HTTP/gRPC/SQL/Redis sem alteração de código.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ App (sem SDK OTel)                                          │
└────────────────────────┬────────────────────────────────────┘
                         │ (eBPF hooks no kernel)
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ otel-ebpf-instrumentation (DaemonSet, privileged)           │
│ Image: ghcr.io/open-telemetry/opentelemetry-ebpf-           │
│        instrumentation/ebpf-instrument:v0.9.0               │
│ ├── Gera traces HTTP/gRPC/SQL/Redis                         │
│ ├── Gera métricas de aplicação + rede                       │
│ └── Exporta OTLP → otel-agent-collector.monitoring:4317     │
└────────────────────────┬────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ otel-agent → gateway → process → Tempo + VictoriaMetrics    │
└─────────────────────────────────────────────────────────────┘
```

## Key behavior

- **Ignora apps com SDK OTel** por default (`exclude_otel_instrumented_services: true`)
- Traces do obi passam pelo tail sampling do agent — mesmas regras (10% PRD, 100% errors/high-latency)
- Traces alimentam o span_metrics connector no otel-process (service graph)
- Config via YAML file montado como ConfigMap (não env vars)
- Não é um OTel Collector — binário standalone com config proprietária

## Deployment (<org> pattern)

Manifesto raw em `monitoring/opentelemetry-collector/obi/`:
- `collector.yaml` — DaemonSet + ServiceAccount + ClusterRole + ClusterRoleBinding + ConfigMap
- `config.yaml` — Config do obi (injetada via `tpl(readFile(...))`)

Segue mesmo padrão organizacional do `profile/`, `agent/`, `gateway/`, `process/`.

## Discovery — Allow-list por namespace de time

```yaml
discovery:
  exclude_instrument:
    - exe_path: '{*ebpf-instrument*,*otelcol*}'
  instrument:
    - k8s_namespace: 'ai*'
    - k8s_namespace: 'acum*'
    - k8s_namespace: 'apps*'
    - k8s_namespace: 'bm*'
    - k8s_namespace: 'ctp*'
    - k8s_namespace: 'dcp*'
    - k8s_namespace: 'deng*'
    - k8s_namespace: 'devops*'
    - k8s_namespace: 'dpm*'
    - k8s_namespace: 'mdt*'
    - k8s_namespace: 'plg*'
    - k8s_namespace: 'qua*'
```

Novo time → adicionar aqui. Glob aceita `*` como wildcard (ex: `dpm*` pega `dpm`, `dpm-people`, `dpm-benefits`).

### Filtros adicionais disponíveis (não usados por default)

| Filtro | Exemplo |
|--------|---------|
| `k8s_deployment_name` | `'my-deploy*'` |
| `k8s_pod_labels` | `{instrument: obi}` |
| `k8s_pod_annotations` | `{obi.instrument: 'true'}` |
| `open_ports` | `'8080,8443'` |
| `languages` | `'go'`, `'java'` |
| `containers_only` | `true` |

## Context propagation

```yaml
ebpf:
  context_propagation: headers
```

Injeta `traceparent` em HTTP/1.1 requests saindo de apps sem SDK. Cria traces distribuídos E2E entre apps legacy e apps com SDK.

- Modo `headers`: só HTTP headers, não precisa de `hostNetwork` nem `CAP_NET_ADMIN`
- Modo `tcp`: também funciona com HTTPS (injecta no nível TCP), requer `hostNetwork` + `CAP_NET_ADMIN`
- gRPC e HTTP/2: **não suportados** no modo `tcp`

## Network metrics

```yaml
metrics:
  features: ['application', 'network_inter_zone']
network:
  enable: true
  allowed_attributes:
    - k8s.src.owner.name
    - k8s.src.namespace
    - k8s.dst.owner.name
    - k8s.dst.namespace
    - k8s.src.owner.type
    - k8s.dst.owner.type
  cidrs:
    - cidr: 172.30.0.0/16
      name: 'vpc-nv'
    - cidr: 172.25.0.0/16
      name: 'vpc-oh'
    - cidr: 172.28.0.0/16
      name: 'vpc-sp'
    - cidr: 10.0.0.0/16
      name: 'k8s-services'
    - cidr: 169.254.0.0/16
      name: 'aws-link-local'
    - cidr: 0.0.0.0/0
      name: 'external'
```

### Métricas geradas

| Métrica | O que mede |
|---------|-----------|
| `obi_network_flow_bytes_total` | Bytes entre endpoints com src/dst owner e namespace |
| `obi_network_inter_zone_bytes_total` | Bytes cross-AZ (custo AWS ~$0.01-0.02/GB) |

### Controle de cardinalidade

- `allowed_attributes`: agregar por **owner** (Deployment), não por pod individual
- `cidrs`: classificar tráfego em categorias conhecidas (vpc, services, aws, external)

### Filtro de rede — Allow-list por namespace

```yaml
filter:
  network:
    k8s_dst_namespace:
      match: '{ai*,acum*,apps*,bm*,ctp*,dcp*,deng*,devops*,dpm*,mdt*,plg*,qua*}'
    k8s_src_namespace:
      match: '{ai*,acum*,apps*,bm*,ctp*,dcp*,deng*,devops*,dpm*,mdt*,plg*,qua*}'
```

Usa `match` (allow-list) em vez de `not_match` (deny-list) — infra é ignorada automaticamente sem manutenção.

## Routes — Controle de cardinalidade de URL

```yaml
routes:
  ignored_patterns:
    - /healthz
    - /ready
    - /metrics
    - /health
    - /live
    - /ping
  unmatched: heuristic
```

- `ignored_patterns`: dropa traces/métricas de healthchecks (reduz volume 30-50%)
- `patterns`: define templates pra agrupar URLs (ex: `/api/v1/users/{id}`)
- `unmatched: heuristic`: tenta agrupar automaticamente URLs não mapeadas

## Performance tuning

```yaml
ebpf:
  http_request_timeout: 30s   # requests sem resposta → status 408
  high_request_volume: true   # evita drop de eventos em alta carga
  # wakeup_len: 1000          # reduz CPU em alta carga (default: 500)
```

### Quando tunar mais

| Sintoma | Ação |
|---------|------|
| CPU do obi alta | `wakeup_len: 1000-2000` |
| Drops de eventos | `high_request_volume: true` (já ativo) |
| Muitas séries | `attributes.select` para excluir labels |
| Volume de traces alto | `otel_traces_export.sampler` com ratio |
| Protocolos irrelevantes gerando overhead | `instrumentations: ['http', 'grpc']` |

## Metrics features disponíveis

| Feature | Descrição | <org> usa? |
|---------|-----------|----------|
| `application` | http/grpc/sql/redis duration | ✅ Sim |
| `network_inter_zone` | Bytes cross-AZ | ✅ Sim |
| `network` | Flow bytes (L4) | Via `network.enable` |
| `application_service_graph` | Quem chama quem | ❌ Redundante (já existe via spanmetrics connector) |
| `application_span` | Spanmetrics legado | ❌ Redundante |
| `application_span_otel` | Spanmetrics formato OTel | ❌ Redundante |
| `application_host` | Métricas por host | ❌ Irrelevante em K8s |
| `application_span_sizes` | Request/response body sizes | Opcional (futuro) |

## Kubernetes metadata

```yaml
attributes:
  kubernetes:
    enable: true
    meta_restrict_local_node: true  # cada pod obi só guarda metadata do próprio node
```

Labels decorados automaticamente: `k8s.namespace.name`, `k8s.deployment.name`, `k8s.pod.name`, `k8s.node.name`, `k8s.container.name`, etc.

## Instrumentação suportada

| Protocolo | Versões |
|-----------|---------|
| HTTP | 1.0/1.1 (context propagation), 2.0 (sem propagação TCP) |
| gRPC | 1.0+ |
| PostgreSQL | All |
| MySQL | All |
| Redis | All |
| MongoDB | 5.0+ |
| Kafka | All |
| AWS S3/SQS | All |

## Relação com <org> Telemetry Helper (lib corporativa)

- Apps **com SDK** (via helper): obi **ignora** automaticamente (não gera traces duplicados)
- Apps **sem SDK**: obi gera traces + métricas via eBPF
- Intervalo de export de métricas: lib usa 60s (default OTel), obi usa 30s (configurável)
- Ambos exportam para o mesmo endpoint: `otel-agent-collector.monitoring:4317`

## Anti-patterns

- ❌ Usar `k8s_namespace: '*'` — instrumenta infra desnecessariamente
- ❌ Deny-list no network filter — difícil manter, preferir allow-list por namespace
- ❌ `application_service_graph` quando já tem spanmetrics connector — duplica métricas
- ❌ Sampling no obi quando tail sampling no agent já cobre — duplo corte
- ❌ Não usar `meta_restrict_local_node` em clusters grandes — desperdício de memória
- ❌ Network sem `allowed_attributes` — cardinalidade explode (agrega por pod)
- ❌ Não filtrar healthchecks em `routes.ignored_patterns` — volume inútil

## Local docs

Documentação completa em:
```
01-DEVOPS/EXTERNAL-DOCS/opentelemetry.io/content/en/docs/zero-code/obi/
├── configure/    # Todas as opções de config
├── setup/        # Kubernetes, Docker, standalone
├── metrics.md    # Métricas emitidas
├── network/      # Network observability
└── distributed-traces.md
```
