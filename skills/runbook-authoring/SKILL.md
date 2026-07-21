---
name: runbook-authoring
description: Use when writing operational runbooks for alerts and incident response at <org>. Covers structure, Alertmanager integration, diagnostics commands, mitigation steps, escalation, and copy-paste template.
keywords: [runbook-authoring, runbook, authoring, "runbook authoring", alertmanager, incident]
---
# Runbook Authoring

Standards for writing and maintaining operational runbooks at <org>. Every production alert MUST have a linked runbook.

## Runbook Purpose

A runbook answers: **"This alert fired — what do I do NOW?"**

It's written for the on-call responder at 3 AM who may not be the service expert. Must be:
- **Copy-pasteable** — commands ready to run (no placeholders without explanation)
- **Step-by-step** — ordered from diagnosis to mitigation
- **Self-contained** — doesn't require reading 5 other docs first
- **Current** — updated after every incident that reveals gaps

## Runbook Structure

### Required Sections

| Section | Purpose |
|---------|---------|
| **Alert Name** | Exact VMRule alert name (for searchability) |
| **Severity** | Expected severity when this fires |
| **Symptoms** | What the user/system experiences |
| **Dashboard Links** | Grafana URLs with relevant panels |
| **Diagnostics** | Commands to understand current state |
| **Root Causes** | Common causes (ordered by likelihood) |
| **Mitigation Steps** | How to fix (ordered by speed/safety) |
| **Verification** | How to confirm the fix worked |
| **Escalation** | Who to contact if mitigation fails |
| **History** | Past incidents related to this alert |

## Storage and Linking

### Where to Store

```
gitlab.<org-domain>/devops/runbooks/
├── slo/
│   ├── slo-burn-rate-critical.md
│   ├── slo-burn-rate-high.md
│   └── slo-burn-rate-elevated.md
├── infrastructure/
│   ├── vm-storage-disk-space.md
│   ├── node-not-ready.md
│   ├── pod-crash-loop.md
│   └── certificate-expiry.md
├── workload/
│   ├── dpm-people-api-high-error-rate.md
│   ├── dcp-ingestion-lag.md
│   └── btc-job-failure.md
└── observability/
    ├── otel-collector-dropping.md
    ├── tempo-ingestion-lag.md
    └── vm-insert-backpressure.md
```

### URL Convention

Alert name maps directly to file path:

```
Alert: VMStorageDiskSpaceCritical
  → runbook_url: https://gitlab.<org-domain>/devops/runbooks/-/blob/main/infrastructure/vm-storage-disk-space.md

Alert: SLOBudgetBurnCritical
  → runbook_url: https://gitlab.<org-domain>/devops/runbooks/-/blob/main/slo/slo-burn-rate-critical.md

Alert: DPMPeopleAPIHighErrorRate
  → runbook_url: https://gitlab.<org-domain>/devops/runbooks/-/blob/main/workload/dpm-people-api-high-error-rate.md
```

Pattern: `kebab-case(alert_name)` → file path.

### VMRule Integration

```yaml
apiVersion: operator.victoriametrics.com/v1beta1
kind: VMRule
metadata:
  name: vm-storage-disk
  namespace: monitoring
spec:
  groups:
    - name: vmstorage
      rules:
        - alert: VMStorageDiskSpaceCritical
          expr: vm_free_disk_space_bytes{job=~".*vmstorage.*"} < 10e9
          for: 5m
          labels:
            severity: critical
          annotations:
            summary: "VMStorage disk space critical on {{ $labels.instance }}"
            description: "Free space: {{ $value | humanize1024 }}B remaining"
            runbook_url: "https://gitlab.<org-domain>/devops/runbooks/-/blob/main/infrastructure/vm-storage-disk-space.md"
            grafana_url: "https://grafana.<org-domain>/d/vm-cluster?viewPanel=disk-space"
```

The `runbook_url` annotation flows through:
```
VMAlert → Alertmanager → Slack template (📖 Runbook link)
```

## Runbook Template

```markdown
# [Alert Name]

**Severity**: [critical | warning | info]
**Service**: [service name]
**Cluster**: [which cluster(s)]
**Last updated**: YYYY-MM-DD

## Symptoms

- [What the user experiences]
- [What monitoring shows]
- [Related alerts that may co-fire]

## Dashboard Links

- [Main dashboard](https://grafana.<org-domain>/d/<id>?var-service=<name>)
- [Error rate panel](https://grafana.<org-domain>/d/<id>?viewPanel=<panel-id>)
- [Logs (Loki)](https://grafana.<org-domain>/explore?left={"datasource":"loki","queries":[{"expr":"{service_name=\"<name>\"}"}]})

## Diagnostics

Run these commands to understand current state:

```bash
# Check pod status
kubectl get pods -n <namespace> -l app.kubernetes.io/name=<service> --context <cluster>

# Check recent events
kubectl get events -n <namespace> --sort-by='.lastTimestamp' --context <cluster> | tail -20

# Check logs (last 5 min)
kubectl logs -n <namespace> -l app.kubernetes.io/name=<service> --since=5m --tail=50 --context <cluster>

# Check resource usage
kubectl top pods -n <namespace> -l app.kubernetes.io/name=<service> --context <cluster>

# Query error rate (VictoriaMetrics)
curl -s "https://victoria-metrics-read.<org-domain>/select/0/prometheus/api/v1/query?query=rate(http_server_request_duration_seconds_count{service_name=\"<service>\",http_status_code=~\"5..\"}[5m])"
```

## Root Causes (ordered by likelihood)

| # | Cause | How to confirm |
|---|-------|----------------|
| 1 | [Most common cause] | [Command or check to verify] |
| 2 | [Second most common] | [Command or check to verify] |
| 3 | [Less common] | [Command or check to verify] |

## Mitigation Steps

### Option 1: [Fastest/safest option]

```bash
# [Step-by-step commands]
```

**Expected result**: [What should happen after this]
**Rollback**: [How to undo if it makes things worse]

### Option 2: [Alternative if Option 1 doesn't work]

```bash
# [Step-by-step commands]
```

### Option 3: [Last resort]

```bash
# [Step-by-step commands]
```

⚠️ **Risk**: [What could go wrong with this option]

## Verification

After mitigation, confirm resolution:

```bash
# Check error rate is back to normal
curl -s "https://victoria-metrics-read.<org-domain>/select/0/prometheus/api/v1/query?query=rate(http_server_request_duration_seconds_count{service_name=\"<service>\",http_status_code=~\"5..\"}[5m])"

# Check alert is no longer firing
curl -s "https://alertmanager.<org-domain>/api/v2/alerts?filter=alertname%3D<AlertName>" | jq '.[].status.state'

# Verify health endpoint
kubectl exec -n <namespace> <pod> --context <cluster> -- curl -s localhost:8080/healthz
```

## Escalation

If mitigation fails after 15 minutes:

| Contact | When | How |
|---------|------|-----|
| [Team lead] | First escalation | Slack DM + mention in incident thread |
| [Service owner] | Domain expertise needed | Slack DM |
| [SRE on-call] | Infrastructure issue | `#incidents-active` |

## History

| Date | Incident | Resolution | Post-mortem |
|------|----------|------------|-------------|
| YYYY-MM-DD | [Brief description] | [What fixed it] | [Link] |
```

## Example: VMStorage Disk Space

```markdown
# VMStorageDiskSpaceCritical

**Severity**: critical
**Service**: VictoriaMetrics vmstorage
**Cluster**: <org>-eks-prd (core-devops)
**Last updated**: 2026-05-28

## Symptoms

- VMStorage pods approaching disk capacity
- Potential data loss if disk fills completely
- Write failures visible in vminsert logs

## Dashboard Links

- [VM Cluster Overview](https://grafana.<org-domain>/d/vm-cluster?orgId=1)
- [Disk Usage Panel](https://grafana.<org-domain>/d/vm-cluster?viewPanel=disk-free)

## Diagnostics

```bash
# Check current disk usage
kubectl exec -n monitoring vm-cluster-vmstorage-0 --context core-devops -- df -h /storage

# Check retention and data size
kubectl exec -n monitoring vm-cluster-vmstorage-0 --context core-devops -- ls -lh /storage/data/

# Check ingestion rate (is it spiking?)
curl -s "https://victoria-metrics-read.<org-domain>/select/0/prometheus/api/v1/query?query=rate(vm_rows_inserted_total[1h])"
```

## Root Causes

| # | Cause | How to confirm |
|---|-------|----------------|
| 1 | Cardinality explosion (new high-cardinality metric) | Check `vm_new_timeseries_created_total` spike |
| 2 | Retention too long for disk size | Check `-retentionPeriod` vs disk capacity |
| 3 | Ingestion spike (new scrape target) | Check `rate(vm_rows_inserted_total[1h])` |

## Mitigation Steps

### Option 1: Identify and drop high-cardinality metric

```bash
# Find top cardinality contributors
curl -s "https://victoria-metrics-read.<org-domain>/select/0/prometheus/api/v1/status/tsdb" | jq '.data.seriesCountByMetricName[:10]'
```

If a single metric dominates → add `metric_relabel_configs` to drop it (requires helmfile change + approval).

### Option 2: Reduce retention

Current retention: check `-retentionPeriod` in vmstorage args.
Reducing retention frees space as old data expires (not immediate).

### Option 3: Expand PVC (immediate relief)

```bash
# Check StorageClass allows expansion
kubectl get pvc -n monitoring -l app=vmstorage -o jsonpath='{.items[0].spec.storageClassName}'

# Expand (requires approval)
kubectl patch pvc vmstorage-vm-cluster-vmstorage-0 -n monitoring --context core-devops \
  -p '{"spec":{"resources":{"requests":{"storage":"200Gi"}}}}'
```

⚠️ **Risk**: PVC expansion requires pod restart on some StorageClasses.

## Verification

```bash
# Confirm disk usage decreased or stabilized
kubectl exec -n monitoring vm-cluster-vmstorage-0 --context core-devops -- df -h /storage

# Confirm alert resolved
curl -s "https://alertmanager.<org-domain>/api/v2/alerts?filter=alertname%3DVMStorageDiskSpaceCritical" | jq '.[].status.state'
```

## Escalation

| Contact | When | How |
|---------|------|-----|
| DevOps team | PVC expansion needed | `#eks-notifications` |
| SRE | Cardinality investigation | `#incidents-active` |

## History

| Date | Resolution | Post-mortem |
|------|------------|-------------|
| 2026-04-15 | Dropped BigBoost_* metrics (high cardinality) | [Link] |
```

## Auto-Discovery Convention

### Pattern

```
alert_name (PascalCase) → kebab-case → directory/file.md
```

Examples:
- `SLOBudgetBurnCritical` → `slo/slo-budget-burn-critical.md`
- `VMStorageDiskSpaceCritical` → `infrastructure/vm-storage-disk-space-critical.md`
- `KubePodCrashLooping` → `infrastructure/kube-pod-crash-looping.md`

### Validation Script

```bash
#!/bin/bash
# Check all alerts have runbook_url pointing to existing files
kubectl get vmrules -n monitoring -o json | \
  jq -r '.items[].spec.groups[].rules[] | select(.alert) | .annotations.runbook_url // "MISSING: \(.alert)"' | \
  while read url; do
    if [[ "$url" == MISSING* ]]; then
      echo "❌ $url"
    else
      path=$(echo "$url" | sed 's|.*/runbooks/-/blob/main/||')
      if [ ! -f "runbooks/$path" ]; then
        echo "⚠️  File not found: $path (from $url)"
      fi
    fi
  done
```

## Maintenance Rules

- **Update after every incident** — if the runbook was wrong or incomplete, fix it immediately
- **Review quarterly** — commands may be outdated (namespace changes, tool upgrades)
- **Test commands** — run diagnostics commands periodically to ensure they still work
- **Version control** — runbooks in GitLab, changes via MR (audit trail)
- **Ownership** — each runbook has a team owner (in frontmatter or CODEOWNERS)

## Anti-patterns

- ❌ **Stale runbook** — commands reference old namespaces, deleted services, or wrong clusters
- ❌ **Query-only runbook** — lists PromQL queries but no mitigation steps ("now what?")
- ❌ **"Page person X"** — runbook that just says "contact John" (John is on vacation)
- ❌ **No verification step** — responder mitigates but can't confirm it worked
- ❌ **Placeholders without explanation** — `kubectl logs <pod>` without saying how to find the pod name
- ❌ **Single mitigation path** — only one option; if it fails, responder is stuck
- ❌ **No escalation path** — responder doesn't know who to contact next
- ❌ **Runbook in someone's head** — "ask the team" is not a runbook
- ❌ **Alert without runbook_url** — responder must search for docs during incident (wastes time)
- ❌ **Runbook in wiki nobody can find** — must be linked directly from alert annotation
- ❌ **Copy-paste from Stack Overflow** — commands without <org> context (wrong cluster, wrong namespace)

## Reference

- Related skills: `alerting-strategy`, `incident-response-runbook`, `vmalert-configuration`
- <org> runbooks repo: `gitlab.<org-domain>/devops/runbooks/`
- Alertmanager: `https://alertmanager.<org-domain>`
- Grafana: `https://grafana.<org-domain>`
- VictoriaMetrics read: `https://victoria-metrics-read.<org-domain>/select/0/prometheus`
