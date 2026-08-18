---
name: fluent-bit-loki-pipeline
description: "Fluent Bit configuration for log collection at <org>. Use when configuring labels vs structured metadata, multiline parsers (.NET, Go, Java stacktraces), or troubleshooting log pipeline. Covers <org> standard config, Loki output plugin, kubernetes filter, multiline parsing."
keywords: [fluent-bit-loki-pipeline, fluent, bit, loki, pipeline, "fluent bit", "bit loki", "loki pipeline", go, net]
---
# Fluent Bit Log Pipeline (<org> Standard)

Configuration patterns for Fluent Bit → Loki at <org>.

## Config location

```
<workspace>/02-KUBE/00-CONFIG/k8s-setup/monitoring/fluent-bit-collector/
├── values.yaml.gotmpl       # Core cluster config
├── values-dev.yaml.gotmpl   # Dev cluster overrides
└── values-prd.yaml.gotmpl   # Prd cluster overrides
```

Helm chart: `fluent/fluent-bit` version 1.0.5

## Labels vs Structured Metadata — decision framework

| Criteria | Labels | Structured Metadata |
|----------|--------|---------------------|
| Indexed | ✅ Yes | ❌ No |
| Creates streams | ✅ Yes (each unique combo = 1 stream) | ❌ No |
| Query speed | Fast (index lookup) | Slower (scan within stream) |
| Cardinality limit | LOW (< 100 unique values) | HIGH (unlimited) |
| Memory/disk cost | High per unique combination | Minimal |
| Query syntax | `{label="value"}` | `{stream_label="x"} \| field="value"` |

### What goes where

| Field | Where | Rationale |
|-------|-------|-----------|
| `eks_cluster` | Label | ~3 values |
| `service_namespace` | Label | ~25 values |
| `service_workload` | Label | ~50-200 values (acceptable) |
| `k8s_pod_name` | Label | High cardinality but needed for pod queries |
| `trace_id`, `span_id` | Structured Metadata | Extremely high cardinality |
| `container_name` | Structured Metadata | Medium cardinality |
| `node_name` | Structured Metadata | Medium cardinality |

## <org> standard config

### Labels (indexed, low cardinality)

```yaml
labels:
  eks_cluster: 'core'  # static per cluster
  service_namespace: $kubernetes['namespace_name']
  k8s_pod_name: $kubernetes['pod_name']
  service_name: $service_name
  service_workload: $kubernetes['labels']['app.kubernetes.io/name']
  service_workload_component: $kubernetes['labels']['app.kubernetes.io/component']
  service_workload_instance: $kubernetes['labels']['app.kubernetes.io/instance']
```

### Structured metadata (not indexed)

```yaml
structured_metadata: $detected_level, container_name=$kubernetes['container_name']
```

### Critical settings

```yaml
drop_single_key: on
line_format: key_value
auto_kubernetes_labels: false  # CRITICAL: prevents label explosion from pod labels
remove_keys: time, stream, _p, kubernetes
```

| Setting | Why |
|---------|-----|
| `auto_kubernetes_labels: false` | Without this, ALL pod labels become Loki labels — explosive cardinality |
| `remove_keys` | Strips CRI wrapper and k8s metadata from log body (already extracted as labels) |
| `drop_single_key: on` | If only `log` key remains, sends its value directly (cleaner output) |

## Multiline parsing

### Custom parser: `dotnet-stacktrace`

```yaml
multilineParsers:
  - name: dotnet-stacktrace
    type: regex
    flush_timeout: 1000
    rules:
      - state: start_state
        regex: '/^(?!\s+at\s|---\s|.*Exception:?\s).*$/'
        next_state: cont
      - state: cont
        regex: '/^(\s+at\s|---\s|.*Exception:?\s|.*--->\s)/'
        next_state: cont
```

### Input multiline parsers

```yaml
[INPUT]
    Name              tail
    multiline.parser  cri, docker, dotnet-stacktrace, go, java
```

Concatenates:
1. CRI partial messages (split by container runtime)
2. Docker partial messages
3. .NET stacktraces (`   at ...`, `--- ...`, `Exception`)
4. Go panics (`goroutine`, `panic:`)
5. Java stacktraces

### Built-in parsers available
`cri`, `docker`, `go`, `java`, `python`, `ruby`

### Custom parser rules
- `type: regex` with `start_state` and continuation rules
- `buffer: on` required when using as filter (NOT needed in Tail input directly)
- Multiline filter MUST be the FIRST filter (re-emits to pipeline head)

## Loki output plugin — processing order

1. Extract `labels` (record accessors evaluated)
2. Extract `structured_metadata` (record accessors evaluated)
3. Apply `remove_keys`
4. Apply `drop_single_key` (if only one key remains, use its value)
5. Format remaining record per `line_format`

**Key insight**: `remove_keys` happens AFTER label/structured_metadata extraction. So you CAN reference `$kubernetes['host']` in structured_metadata even with `remove_keys: kubernetes`.

## drop_single_key behavior

- Activates when **exactly one key** remains after remove_keys
- With `remove_keys: time, stream, _p, kubernetes` → only `log` remains → `drop_single_key: on` sends the `log` value
- `on` = quoted string if line_format is json
- `raw` = unquoted

## structured_metadata_map_keys (use with caution)

Alternative — sends all entries from a map:
```yaml
structured_metadata_map_keys: $kubernetes
```
**High cardinality risk**. Avoid unless you understand the impact.

## Log format by namespace (<org> observed)

| Namespace | Log format | Level field | Multiline risk |
|-----------|-----------|-------------|----------------|
| argo | JSON | `level` + `msg` | Low |
| defectdojo | Plain text (uwsgi) | None | Low |
| nginx | JSON | `status` (HTTP code) | Low |
| kyverno | JSON | `level` + `message` | Low |
| istio-system (ztunnel) | JSON | `level` + `message` | Low |
| devops (sample-api) | .NET stderr | N/A | **HIGH** |
| dpm-crons | .NET exceptions | `Exception:` | **HIGH** |

## Common gotchas

### `detected_level=unknown` for nested JSON
Loki's auto-detection can't parse JSON inside the `log` field. Solutions:
1. Enable `merge_log: true` in kubernetes processor → expand log JSON → extract `level`
2. Wait for Loki to improve detection
3. Use Lua filter to extract level from JSON content

### `service_workload` empty for some namespaces (e.g., kyverno)
Kyverno pods don't have `app.kubernetes.io/name`. Solution: Lua filter fallback that extracts workload name from pod name regex.

### Excluded namespace still appearing in logs
Check timing: filter may have been applied AFTER pod was scraped. Restart fluent-bit to pick up new exclusion rules.

## Future consideration: filelog receiver migration

Replacing Fluent Bit with OTel Collector `filelog` receiver would:
- Unify naming (k8s.namespace.name vs kubernetes.namespace_name)
- Enable same `k8sattributesprocessor` for logs (consistent with metrics/traces)
- Single tool to maintain

See related skill: `fluent-bit-vs-otel-logs`.

## Reference

- Local docs: `<workspace>/01-DEVOPS/EXTERNAL-DOCS/fluent-bit-docs`
- Loki output: `pipeline/outputs/loki.md`
- Multiline filter: `pipeline/filters/multiline-stacktrace.md`
- Multiline parsers: `pipeline/parsers/multiline-parsing.md`
- Kubernetes filter: `pipeline/filters/kubernetes.md`
- Lua filter: `pipeline/filters/lua.md`
- Tail input: `pipeline/inputs/tail.md`
