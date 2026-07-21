---
name: alertmanager-slack-config
description: Alertmanager configuration with Slack integration. Use when configuring receivers, routing, templates with Grafana links and runbook URLs, or troubleshooting silent alerts. Covers <org> standard config, helmfile escaping, secret regeneration patterns.
keywords: [alertmanager-slack, alertmanager, slack, "alertmanager slack", grafana, helmfile]
---
# Alertmanager Slack Configuration

Standard config for Alertmanager → Slack at <org>.

## Standard receiver config

```yaml
# kube-prometheus-stack/values.yaml.gotmpl
alertmanager:
  config:
    receivers:
      - name: slack-critical-warning
        slack_configs:
          - api_url_file: /etc/alertmanager/secrets/slack-webhook
            channel: '#eks-notifications'
            send_resolved: true
            title: '{{ template "slack.title" . }}'
            text: '{{ template "slack.text" . }}'

    route:
      group_by: ['alertname', 'cluster', 'namespace']
      group_wait: 30s
      group_interval: 5m
      repeat_interval: 4h  # Default
      receiver: slack-critical-warning
      routes:
        - matchers:
            - severity =~ "critical|warning"
          receiver: slack-critical-warning
        - matchers:
            - alertname = VMStorageDiskSpaceCritical
          repeat_interval: 1h  # More frequent for disk space
        - matchers:
            - alertname = Watchdog
          receiver: 'null'  # Silence watchdog
        - matchers:
            - alertname =~ "InfoInhibitor|KubeControllerManagerDown|KubeSchedulerDown|KubeAPIDown|ArgocdServiceNotSynced"
          receiver: 'null'  # EKS-managed or noisy alerts
```

## Slack template

```yaml
templateFiles:
  slack.tmpl: |
    {{`
    {{ define "slack.title" }}
    {{ if eq .Status "firing" }}🔥{{ else }}✅{{ end }} [{{ .Status | toUpper }}:{{ .Alerts.Firing | len }}] {{ .CommonLabels.alertname }}
    {{ end }}

    {{ define "slack.text" }}
    *Cluster:* {{ .CommonLabels.cluster }}
    *Severity:* {{ .CommonLabels.severity }}

    {{ range .Alerts }}
    • *Namespace:* {{ .Labels.namespace }}{{ if .Labels.pod }} *Pod:* {{ .Labels.pod }}{{ end }}
      {{ .Annotations.description }}
    {{ end }}

    {{ with (index .Alerts 0) }}{{ if .Annotations.runbook_url }}<{{ .Annotations.runbook_url }}|📖 Runbook>{{ end }} {{ if .GeneratorURL }}<{{ .GeneratorURL }}|📊 Grafana>{{ end }}{{ end }}
    {{ end }}
    `}}
```

### Key points
- `🔥 FIRING` / `✅ RESOLVED` icons in title
- Cluster + severity at top
- Per-alert details (namespace, pod)
- **Footer with Grafana link** (from `GeneratorURL` set by vmalert)
- **Optional runbook link** (from `runbook_url` annotation)

## Helmfile escaping (IMPORTANT)

Templates pass through 2-3 template engines:
1. **helmfile** (gotmpl/Sprig) — renders `values.yaml.gotmpl`
2. **Helm** — renders chart templates

Escaping in helmfile gotmpl:
- All `{{` and `}}` in the Slack template must be wrapped in `{{` + `` ` `` + `}}` syntax
- Use the outermost `{{` "..." `}}` raw string OR escape per occurrence

Pattern:
```yaml
templateFiles:
  slack.tmpl: |
    {{`
    {{ ... go template content ... }}
    `}}
```

The outer `{{` ` `}}` makes helmfile pass everything inside as a literal string to Helm.

## Annotations with $labels / $value

Annotations using `{{ $labels.foo }}` or `{{ $value }}` need DOUBLE escaping when processed by helmfile + Helm chart:

```yaml
# In VMRule (no helmfile escaping needed at this layer):
annotations:
  description: 'Pod {{ $labels.pod }} has restarted {{ $value }} times'

# In helmfile.yaml.gotmpl values (NEEDS escaping):
annotations:
  description: 'Pod {{`{{ $labels.pod }}`}} has restarted {{`{{ $value }}`}} times'
```

**Easier alternative**: use static text in annotations, dynamic context comes via `vmalert.external.alert.source` (Grafana link).

## Slack channels at <org>

| Channel | Receiver | Purpose |
|---------|----------|---------|
| `#eks-notifications` | slack-critical-warning | General cluster alerts |
| `#eks-notifications-argo` | (separate) | ArgoCD sync issues |
| `#eks-notifications-teams` | (separate) | Team-specific alerts |
| `#eks-notifications-workload-dev` | (separate) | Dev workload alerts |
| `#eks-notifications-workload-prd` | (separate) | Prd workload alerts |

## Secret management

App token via Kubernetes secret: `alertmanager-slack-webhook`

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: alertmanager-slack-webhook
  namespace: monitoring
data:
  slack-webhook: <base64-encoded-webhook-url>
```

Mount in Alertmanager pod, reference via `api_url_file: /etc/alertmanager/secrets/slack-webhook`.

**NEVER** put `api_url:` (literal) in values.yaml — webhook ends up in helm history and ConfigMap.

## prometheus-operator regex validation

The operator validates regex patterns in alertmanager routes. Common bug:

```yaml
# ❌ WRONG — fails with: error parsing regexp: missing argument to repetition operator: `*`
matchers:
  - namespace =~ "*.events"

# ✅ CORRECT
matchers:
  - namespace =~ ".*events"
```

`*` needs a preceding character or escape.

## Secret regeneration

The operator generates `alertmanager-<name>-generated` secret from the source. To force regeneration after config changes:

```bash
kubectl -n monitoring delete secret alertmanager-prometheus-alertmanager-generated
# Operator recreates immediately

# If pod doesn't pick up new config, restart
kubectl -n monitoring delete pod alertmanager-prometheus-alertmanager-0
```

## Inhibition rules (avoid noisy alerts)

```yaml
inhibit_rules:
  - source_matchers:
      - severity = critical
    target_matchers:
      - severity = warning
    equal: [alertname, cluster, namespace]
```

If a critical alert fires, suppress warnings for the same alert+cluster+namespace.

## Silences

For temporary silencing (e.g., maintenance window), use Alertmanager API or Grafana UI — not config changes.

```bash
# Via amtool
amtool silence add alertname="MyAlert" --duration=1h --comment="Planned maintenance"
```

## Common issues

### Issue: alerts firing but no Slack notification
Check:
1. `kubectl logs alertmanager-... -n monitoring` for delivery errors
2. Webhook secret correct? `kubectl get secret alertmanager-slack-webhook -o jsonpath='{.data.slack-webhook}' | base64 -d | head -c 30`
3. Receiver match? Test with `amtool config routes test ...`
4. Watchdog rule actually firing (heartbeat alert)?

### Issue: messages garbled / template errors
Check helmfile escaping — most common cause. Run `helmfile template` and inspect generated config.

### Issue: alerts grouping incorrectly
Adjust `group_by` in route. Common: `group_by: ['alertname', 'cluster', 'namespace']`.

## Reference

- Alertmanager docs: https://prometheus.io/docs/alerting/latest/configuration/
- Slack template syntax: https://prometheus.io/docs/alerting/latest/notifications/
- Related skills: `vmalert-configuration` (for `external.alert.source` Grafana links)
