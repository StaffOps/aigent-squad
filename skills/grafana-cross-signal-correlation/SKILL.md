---
name: grafana-cross-signal-correlation
description: Grafana datasource configuration for cross-signal correlation (metric→trace→log→profile). Use when setting up exemplars, tracesToLogsV2, tracesToMetrics, derivedFields, or service map. Covers all <org> datasource UIDs and required configuration.
keywords: [grafana-signal-correlation, grafana, signal, correlation, "grafana signal", "signal correlation"]
---
# Grafana Cross-Signal Correlation

How to configure Grafana datasources at <org> so users can navigate seamlessly between metrics, traces, logs, and profiles.

## Datasources at <org> (confirmed May 2026)

| Name | Type | UID | URL |
|------|------|-----|-----|
| VictoriaMetrics | prometheus | `victoriametrics` | http://vm-cluster-vmselect.monitoring:8481/select/0/prometheus/ |
| Tempo | tempo | `tempo` | http://tempo-query-frontend.monitoring:3200 |
| Loki | loki | `loki` | http://loki-gateway.monitoring:80 |
| Pyroscope | grafana-pyroscope-datasource | `pyroscope` | http://pyroscope-query-frontend.monitoring:4040 |
| Alertmanager | alertmanager | `alertmanager` | http://prometheus-alertmanager.monitoring:9093/ |

The UIDs are stable — use them for cross-references.

## Cross-signal correlations

### 1. VictoriaMetrics → Tempo (exemplars)

Click on a metric spike → jump to the exact trace.

```yaml
# datasources/victoriametrics.yaml
exemplarTraceIdDestinations:
  - name: traceID
    datasourceUid: tempo
```

App side requirement: enable exemplars in OTel SDK.
```csharp
// .NET
.SetExemplarFilter(ExemplarFilterType.TraceBased)
```

### 2. Tempo → Loki (trace to logs)

From a span, click "Logs for this span" to query Loki by trace ID.

```yaml
# datasources/tempo.yaml
tracesToLogsV2:
  datasourceUid: loki
  spanStartTimeShift: -1h
  spanEndTimeShift: 1h
  tags:
    - key: service.name
      value: service_name  # Tempo uses service.name, Loki labels use service_name
  filterByTraceID: true
  filterBySpanID: false
```

The `tags` mapping translates between OTel attribute names (`service.name`) and Loki labels (`service_name` — dots converted to underscores in Prometheus/Loki).

### 3. Tempo → VictoriaMetrics (trace to metrics)

From a span, view related RED metrics (rate/error/duration).

```yaml
tracesToMetrics:
  datasourceUid: victoriametrics
  tags:
    - key: service.name
      value: service_name
  queries:
    - name: 'Sample query'
      query: 'sum(rate(spanmetrics_apm_calls_total{service_name="$__tags.service_name"}[5m]))'
```

Requires: OTel Collector with `spanmetrics` connector emitting `spanmetrics_apm_*` metrics.

### 4. Tempo → Pyroscope (trace to profiles)

From a span, view CPU profile during that span's execution.

```yaml
tracesToProfiles:
  datasourceUid: pyroscope
  tags:
    - key: service.name
      value: service_name
  profileTypeId: process_cpu:cpu:nanoseconds:cpu:nanoseconds
```

Requires: profiling enabled in app (currently stand-by at <org>; Pyroscope SDK removed pending OTel native Profiles signal).

### 5. Tempo Service Map

Visual topology of services (node graph).

```yaml
serviceMap:
  datasourceUid: victoriametrics
nodeGraph:
  enabled: true
```

Requires: `spanmetrics` connector emitting service graph metrics (`traces_service_graph_request_total`).

### 6. Loki → Tempo (log to trace via derivedFields)

From a log line containing a trace ID, click to open in Tempo.

```yaml
# datasources/loki.yaml
derivedFields:
  - matcherRegex: 'traceID=(\w+)'
    name: TraceID
    url: '$${__value.raw}'
    datasourceUid: tempo
  - matcherRegex: 'trace_id=(\w+)'  # snake_case variant
    name: TraceID
    url: '$${__value.raw}'
    datasourceUid: tempo
  - matcherRegex: '"traceId":"(\w+)"'  # JSON variant
    name: TraceID
    url: '$${__value.raw}'
    datasourceUid: tempo
```

**IMPORTANT — apps must emit traceID in logs**: this only works if the log line contains the trace ID. With <org> OTel Helper, native ILogger integration adds `traceId`/`spanId` to scopes, which appear in stdout JSON.

## Required Grafana change at <org>

Add `derivedFields` to Loki datasource (currently missing). Without it, log → trace correlation doesn't work.

## Spanmetrics naming

OTel converts dots to underscores when exporting to Prometheus/VM:
- OTel: `spanmetrics.calls.total`
- VM: `spanmetrics_apm_calls_total` (with namespace prefix `apm_`)

Resource attributes become labels:
- OTel resource: `service.name="checkout-api"`
- VM label: `service_name="checkout-api"`

To use `service.workload` and `service.namespace` for APM/resource correlation:
```yaml
# OTel Collector — process collector with spanmetrics connector
connectors:
  spanmetrics:
    resource_metrics_key_attributes:
      - service.name
      - service.workload    # k8s.deployment.name via k8sattributes
      - service.namespace   # pod namespace
      - deployment.environment
      - eks_cluster
```

This produces `spanmetrics_apm_calls_total{service_name=..., service_workload=..., service_namespace=...}` which can join with cadvisor metrics by namespace+workload.

## Dashboard variables for correlation

Standard variable chain:
```yaml
- name: service
  query: label_values(spanmetrics_apm_calls_total, service_name)
- name: service_workload
  query: label_values(spanmetrics_apm_calls_total{service_name="$service"}, service_workload)
- name: service_namespace
  query: label_values(spanmetrics_apm_calls_total{service_name="$service"}, service_namespace)
```

Then queries can join APM metrics with cadvisor:
```promql
# APM rate
sum(rate(spanmetrics_apm_calls_total{service_name="$service"}[5m]))

# Pod CPU (joined via workload+namespace)
sum(rate(container_cpu_usage_seconds_total{
  namespace="$service_namespace",
  pod=~"$service_workload-.*"
}[5m]))
```

## Provisioning datasources

Datasources at <org> are provisioned via Grafana ConfigMaps from the kube-prometheus-stack chart. To modify:

```yaml
# values.yaml.gotmpl
grafana:
  additionalDataSources:
    - name: Tempo
      type: tempo
      uid: tempo
      url: http://tempo-query-frontend.monitoring:3200
      access: proxy
      jsonData:
        tracesToLogsV2:
          datasourceUid: loki
          tags:
            - key: service.name
              value: service_name
          filterByTraceID: true
        # ... etc
```

## Reference

- Grafana datasource provisioning: https://grafana.com/docs/grafana/latest/administration/provisioning/
- Tempo datasource: https://grafana.com/docs/grafana/latest/datasources/tempo/
- Spanmetrics connector: https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/connector/spanmetricsconnector
- Local docs: `<workspace>/01-DEVOPS/EXTERNAL-DOCS/{tempo,loki,pyroscope}/docs`
