---
name: vmalert-configuration
description: "VMAlert configuration including extraArgs, evalDelay, queryStep, and Grafana link generation in alert source. Use when configuring VMRule resources, debugging alert evaluation, or setting up HA. Covers vm-operator CRD specifics and the helmfile triple-template escaping for `external.alert.source`."
keywords: [vmalert, alert, grafana, helmfile, ha, crd]
---
# VMAlert Configuration

VMAlert is the rule evaluator that replaces Prometheus's built-in rule evaluator. Configures recording rules, alerting rules, and notifies Alertmanager.

## VMAlert CRD basics

```yaml
apiVersion: operator.victoriametrics.com/v1beta1
kind: VMAlert
metadata:
  name: vmalert
  namespace: monitoring
spec:
  replicaCount: 1  # 2 for HA (see HA section)
  datasource:
    url: http://vmselect.monitoring:8481/select/0/prometheus
  notifiers:
    - url: http://prometheus-alertmanager.monitoring:9093
  remoteWrite:
    url: http://vminsert.monitoring:8480/insert/0/prometheus
  remoteRead:
    url: http://vmselect.monitoring:8481/select/0/prometheus
  evaluationInterval: 1m
  selectAllByDefault: true  # Pick up all VMRule CRDs
```

## Critical extraArgs

```yaml
spec:
  extraArgs:
    external.url: 'https://grafana.<org-domain>'
    external.alert.source: '<escaped Go template — see below>'
    rule.evalDelay: "30s"
    datasource.queryStep: "1m"
    configCheckInterval: "30s"
    datasource.maxIdleConnections: "100"
```

### What each flag does

| Flag | Purpose |
|------|---------|
| `external.url` | Base URL for alert source links (prepended to `external.alert.source`) |
| `external.alert.source` | Go template generating Grafana Explore link |
| `rule.evalDelay` | Compensates data ingestion delay (30s default). Match with vmselect `-search.latencyOffset` |
| `datasource.queryStep` | Step param for instant queries. **Set to 2x scrape interval** (e.g., 1m for 30s scrapes) to avoid "no data" |
| `configCheckInterval` | Hot-reload interval for VMRules (no SIGHUP needed) |
| `datasource.maxIdleConnections` | Connection pool size (rules × concurrency) |

## external.alert.source — Grafana link generator

The template generates a Grafana Explore link from the alert. This URL goes to `GeneratorURL` in the alert payload, which Alertmanager passes to Slack templates.

### Final template

```
explore?orgId=1&left={"datasource":"VictoriaMetrics","queries":[{"expr":{{.Expr|jsonEscape|queryEscape}},"refId":"A"}],"range":{"from":"now-1h","to":"now"}}{{ if .Labels.cluster }}&var-cluster={{.Labels.cluster}}{{ end }}{{ if .Labels.namespace }}&var-namespace={{.Labels.namespace}}{{ end }}
```

Generates URLs like:
`https://grafana.<org-domain>/explore?orgId=1&left={"datasource":"VictoriaMetrics","queries":[{"expr":"up%3D%3D0","refId":"A"}],"range":{"from":"now-1h","to":"now"}}&var-cluster=<org>-eks-prd&var-namespace=monitoring`

When user clicks 📊 Grafana in Slack, opens Grafana Explore with the rule expression pre-loaded.

## Helmfile triple-template escaping

The `external.alert.source` template passes through **3 template engines**:
1. **helmfile** (gotmpl/Sprig) — renders `values.yaml.gotmpl`
2. **Helm** — renders chart templates
3. **Helm `tpl`** — vm-operator chart calls `tpl` on `extraObjects`

Any `{{}}` would be interpreted by one of these layers. **Critical to escape correctly**.

### Solution

In `helmfile.yaml.gotmpl` (environment values), use helmfile's raw string syntax:

```yaml
vmalert_external_source: 'explore?...{{`{{`}}.Expr|jsonEscape|queryEscape{{`}}`}}...{{`{{`}} if .Labels.cluster {{`}}`}}&var-cluster={{`{{`}}.Labels.cluster{{`}}`}}{{`{{`}} end {{`}}`}}...'
```

- `{{` + `` `{{` `` + `}}` → helmfile renders this as literal `{{`
- Result after helmfile: `{{.Expr|jsonEscape|queryEscape}}`

In `vmalert-resource.yaml`, reference and re-wrap for Helm's `tpl`:

```yaml
external.alert.source: '{{ printf "{{`%s`}}" .Values.vmalert_external_source }}'
```

- helmfile resolves `.Values.vmalert_external_source` (injects the value)
- `printf "{{` + `"` + ` `%s` ` + `"` + `}}"` wraps in `` {{`...`}} ``
- Helm's `tpl` sees `` {{`...`}} `` and renders as literal string (raw string in Go templates)
- vmalert receives the unmodified Go template

### Key rule

**NEVER put raw `{{}}` in extraObjects YAML** — `tpl` will always interpret. Use the escaping chain above.

## VMAlert HA setup (NOT yet applied at <org>)

For future HA:

```yaml
spec:
  replicaCount: 2

# Configure vmselect with deduplication
# (matches evaluationInterval)
vmselect.extraArgs:
  dedup.minScrapeInterval: "30s"

# Use multiple notifiers (Alertmanager pod FQDNs or static)
notifiers:
  - url: http://alertmanager-0.alertmanager-headless.monitoring:9093
  - url: http://alertmanager-1.alertmanager-headless.monitoring:9093
```

Alertmanager automatically deduplicates identical alerts received from multiple VMAlert replicas.

## VMRule examples

### Alerting rule

```yaml
apiVersion: operator.victoriametrics.com/v1beta1
kind: VMRule
metadata:
  name: vm-disk-space
  namespace: monitoring
spec:
  groups:
    - name: vmstorage-disk
      rules:
        - alert: VMStorageDiskSpaceCritical
          expr: vm_free_disk_space_bytes{job=~".*vmstorage.*"} < 10e9
          for: 5m
          labels:
            severity: critical
          annotations:
            summary: VMStorage running out of disk space
            description: vmstorage disk free space is below 10GB
            runbook_url: https://wiki.<org-domain>/observability/vmstorage-disk
```

### Recording rule

```yaml
spec:
  groups:
    - name: container-cpu-recording
      rules:
        - record: namespace:container_cpu_usage_seconds:sum_rate
          expr: sum by (namespace, cluster) (rate(container_cpu_usage_seconds_total{job="kubelet"}[5m]))
```

## Common flags reference

| Flag | Default | Notes |
|------|---------|-------|
| `-evaluationInterval` | 1m | How often rules are evaluated |
| `-rule.evalDelay` | 30s | Compensate data delay |
| `-datasource.queryStep` | 5m | Step for instant queries (set to 2x resolution) |
| `-configCheckInterval` | disabled | Auto-reload rules (alternative to SIGHUP) |
| `-external.url` | hostname | Base URL for source links |
| `-external.alert.source` | vmalert UI | Go template for source link |
| `-external.label` | none | Labels added to all rules/alerts |
| `-remoteWrite.concurrency` | 2×CPU | Writers for remote write |
| `-remoteWrite.maxBatchSize` | 10000 | Max timeseries per flush |
| `-remoteWrite.maxQueueSize` | 100000 | Max pending datapoints |
| `-remoteRead.lookback` | 1h | How far back to restore alert state |
| `-datasource.maxIdleConnections` | 100 | Idle connection pool |
| `-group.maxStartDelay` | 5m | Smooths load on datasource |

## Common issues

### Issue: "no data" on instant queries
Cause: `datasource.queryStep` < 2x scrape interval.
Fix: increase to 1m (for 30s scrapes) or 2m (for 60s).

### Issue: alerts fire intermittently for healthy targets
Cause: ingestion delay — rule evaluates BEFORE data arrives.
Fix: increase `rule.evalDelay` to 30-60s.

### Issue: rules not picked up after VMRule update
Cause: configCheckInterval not set, or rule has syntax error.
Fix: set `configCheckInterval: "30s"`. Check vmalert logs for parse errors.

### Issue: Grafana link in Slack opens wrong page
Cause: helmfile escaping wrong, template rendered prematurely.
Fix: run `helmfile template` and inspect actual template received by VMAlert.

### Issue: external.alert.source contains `{}` literal
Cause: incomplete escaping — Helm `tpl` interpreted `{{}}` as empty.
Fix: re-check the triple-template escaping chain.

## Reference

- VMAlert docs: https://docs.victoriametrics.com/vmalert/
- Local cache: `<workspace>/01-DEVOPS/EXTERNAL-DOCS/VictoriaMetrics/docs/vmalert*`
- vm-operator CRD reference: https://docs.victoriametrics.com/operator/api/
- Related skills: `alertmanager-slack-config`, `helmfile-templating`
