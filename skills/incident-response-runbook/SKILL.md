---
name: incident-response-runbook
description: Use when responding to production incidents, defining severity levels, assigning incident roles, or establishing communication cadence. Covers severity definitions, IC/Comms/Ops roles, response phases (detect→recover), Slack-based coordination, and <org>-specific tooling.
keywords: [incident-response-runbook, incident, response, runbook, "incident response", "response runbook", ic]
---
# Incident Response Runbook

Structured incident response framework for <org>. Slack-based coordination across 3 EKS clusters (dev, prd-nv, core-devops).

## Severity Definitions

| Level | Impact | Examples | Response Time | Comms Cadence |
|-------|--------|----------|---------------|---------------|
| **SEV1** | Customer-facing outage, revenue impact, data loss risk | Production API down, auth failure, payment processing stopped | 5 min | Every 15 min |
| **SEV2** | Degraded service, partial impact, elevated errors | High error rate (>5%), latency spike (>5x normal), single service down | 15 min | Every 30 min |
| **SEV3** | Internal impact, workaround exists, no customer visibility | Internal tool down, dev environment broken, non-critical batch failure | 1 hour | On resolution |
| **SEV4** | Cosmetic, no functional impact | Dashboard broken, docs wrong, non-blocking CI failure | Next business day | None |

### Severity Decision Tree

```
Is there customer-visible impact?
├── YES → Are customers unable to use the service?
│         ├── YES → SEV1
│         └── NO (degraded but functional) → SEV2
└── NO → Is there internal operational impact?
          ├── YES → SEV3
          └── NO → SEV4
```

### Escalation Rules

- Any responder can **upgrade** severity (never needs approval)
- Only IC can **downgrade** severity (with justification)
- SEV1 auto-escalates to management after 30 min without mitigation
- SEV2 auto-escalates to SEV1 if unresolved after 1 hour

## Incident Roles

| Role | Responsibility | Who |
|------|---------------|-----|
| **Incident Commander (IC)** | Single decision maker. Coordinates all responders. Manages escalation. | On-call SRE or first senior responder |
| **Operations Lead** | Executes technical mitigation. Runs commands, deploys fixes. | Domain expert for affected service |
| **Communications Lead** | Updates stakeholders. Posts status updates. Manages external comms. | IC delegates (or IC handles for SEV3+) |
| **Scribe** | Records timeline in real-time. Captures decisions and actions. | Any available team member |

### IC Responsibilities

1. **Declare** the incident (severity, affected services)
2. **Assign** roles (Ops Lead, Comms Lead, Scribe)
3. **Coordinate** — direct investigation, avoid parallel conflicting actions
4. **Decide** — when to escalate, when to rollback, when to page more people
5. **Communicate** — ensure status updates happen on cadence
6. **Close** — declare "all clear" when service is restored
7. **Initiate** post-mortem within 48h

### IC Does NOT:

- Debug the issue themselves (delegate to Ops Lead)
- Write code or run kubectl commands (unless solo responder)
- Make business decisions (escalate to management)
- Communicate externally without Comms Lead coordination

## Response Phases

### Phase 1: Detect

**Goal**: Identify that an incident is occurring.

Sources:
- Alertmanager → Slack (`#eks-notifications-workload-prd`)
- Customer reports (support tickets, Slack)
- Synthetic monitoring (Uptime Kuma — roadmap)
- Team observation (dashboard anomaly)

Actions:
1. Alert fires → on-call acknowledges in Slack (emoji reaction ✅)
2. Assess severity using decision tree
3. If SEV1-2: proceed to Triage immediately

### Phase 2: Triage

**Goal**: Understand scope and assign roles.

Actions:
1. IC declares incident in `#incidents-active`:
   ```
   🚨 INCIDENT DECLARED
   Severity: SEV1
   Service: dpm-people-api
   Cluster: <org>-workloads-prd-nv
   Impact: API returning 503 for all requests
   IC: @karlipegomes
   ```
2. Create dedicated thread (or channel for SEV1 lasting >1h)
3. Assign roles: Ops Lead, Comms Lead, Scribe
4. Ops Lead begins investigation (logs, metrics, traces)
5. Scribe starts timeline

### Phase 3: Mitigate

**Goal**: Reduce or eliminate customer impact (not necessarily fix root cause).

Mitigation options (ordered by speed):

| Action | Speed | Risk | When to use |
|--------|-------|------|-------------|
| **Rollback** | Fast (2-5 min) | Low | Recent deploy caused issue |
| **Scale up** | Fast (1-2 min) | Low | Capacity issue |
| **Feature flag off** | Fast (<1 min) | Low | New feature causing errors |
| **Restart pods** | Fast (1-2 min) | Medium | Memory leak, stuck state |
| **Redirect traffic** | Medium (5 min) | Medium | Single cluster/AZ issue |
| **Hotfix deploy** | Slow (15-30 min) | High | Only option, no rollback possible |
| **Failover to DR** | Slow (15-60 min) | High | Complete cluster failure |

Rules:
- **Mitigate first, investigate later** — restore service, then find root cause
- IC decides mitigation strategy (Ops Lead executes)
- Document every action in timeline (Scribe)
- Verify mitigation worked (check metrics, not just "it deployed")

### Phase 4: Recover

**Goal**: Confirm service is fully restored and stable.

Actions:
1. Ops Lead confirms metrics are nominal (error rate, latency, throughput)
2. Wait stabilization period (15 min for SEV1, 5 min for SEV2)
3. IC declares "all clear":
   ```
   ✅ ALL CLEAR
   Incident: dpm-people-api outage
   Duration: 23 minutes
   Resolution: Rolled back deployment v1.2.3 → v1.2.2
   Post-mortem: scheduled for [date]
   ```
4. Comms Lead sends final status update to stakeholders
5. Scribe finalizes timeline

### Phase 5: Follow-up

**Goal**: Learn and prevent recurrence.

Actions:
1. IC initiates post-mortem (within 48h for SEV1-2)
2. Post-mortem review meeting (within 5 business days)
3. Action items created in Jira (component: `post-mortem`)
4. Lessons shared in `#postmortem` channel
5. Runbook updated if gap identified

## Communication Patterns

### Slack Channels

| Channel | Purpose | Who posts |
|---------|---------|-----------|
| `#incidents-active` | Active incident coordination | IC, responders |
| `#eks-notifications-workload-prd` | Alert source (automated) | Alertmanager |
| `#eks-notifications-teams` | Team-specific alerts | Alertmanager |
| `#postmortem` | Post-mortem sharing | IC/Author |

### Status Update Template

Posted by Comms Lead at cadence (every 15/30 min):

```
📋 INCIDENT UPDATE — [HH:MM UTC-3]
Status: [Investigating | Mitigating | Monitoring | Resolved]
Severity: SEV[1|2]
Service: [service name]
Impact: [current user impact]
Next update: [HH:MM]
Actions in progress: [what's being done]
```

### Escalation Messages

To management (SEV1 > 30 min):
```
🔴 ESCALATION — SEV1 incident ongoing > 30 min
Service: [name]
Impact: [description]
Current status: [what's been tried]
Need: [decision/resource/approval needed]
```

## <org> Tooling

| Tool | Purpose | Access |
|------|---------|--------|
| **Slack** | Primary coordination | `#incidents-active`, `#eks-notifications-*` |
| **Grafana** | Metrics/traces/logs investigation | `https://grafana.<org-domain>` |
| **Alertmanager** | Alert history, silences | `https://alertmanager.<org-domain>` |
| **ArgoCD** | Deployment status, rollback | Internal |
| **kubectl** | Pod status, logs, restarts | Via kubeconfig (3 clusters) |
| **VictoriaMetrics** | Direct metric queries | `vm-cluster-vmselect.monitoring:8481` |
| **Tempo** | Trace investigation | `tempo-gateway.monitoring:80` |
| **Loki** | Log queries | `loki-gateway.monitoring:80` |

### Quick Investigation Commands

```bash
# Check current context
kubectl config current-context

# Pod status for affected service
kubectl get pods -n <namespace> -l app.kubernetes.io/name=<service>

# Recent events
kubectl get events -n <namespace> --sort-by='.lastTimestamp' | tail -20

# Pod logs (last 5 min)
kubectl logs -n <namespace> -l app.kubernetes.io/name=<service> --since=5m --tail=100

# Previous container logs (if restarting)
kubectl logs -n <namespace> <pod> --previous

# ArgoCD app status
kubectl get applications -n argo -l app.kubernetes.io/name=<service>
```

### Quick Rollback (ArgoCD)

```bash
# Check deployment history
kubectl -n argo get applications <app-name> -o jsonpath='{.status.history}'

# Rollback via ArgoCD (requires approval per k8s-safety steering)
argocd app rollback <app-name> <revision>
```

## Bridge Call Patterns

For SEV1 lasting > 15 min:

1. IC starts Google Meet / Slack huddle
2. Share screen: Grafana dashboard showing affected metrics
3. Mute-by-default (unmute to speak)
4. IC moderates: "Ops Lead, status?" → "Comms Lead, next update due"
5. Scribe captures decisions in thread (not just audio)

Rules:
- Audio is for coordination, NOT for debugging (use Slack threads for async investigation)
- Keep bridge focused — side investigations happen in parallel threads
- IC can dismiss people from bridge when their expertise is no longer needed

## Anti-patterns

- ❌ **Solo heroics** — one person debugging alone for 30+ min without declaring incident
- ❌ **No IC** — everyone investigating independently, conflicting actions
- ❌ **Ad-hoc communication** — DMs instead of public channel (others can't help or learn)
- ❌ **Investigating before mitigating** — spending 20 min finding root cause while users suffer
- ❌ **No status updates** — stakeholders pinging "what's happening?" because no cadence
- ❌ **Severity inflation** — calling everything SEV1 (desensitizes responders)
- ❌ **Severity deflation** — calling SEV1 a SEV3 to avoid process (delays response)
- ❌ **No post-mortem** — "it's fixed, let's move on" (same incident repeats)
- ❌ **IC also debugging** — IC must coordinate, not execute (except solo responder)
- ❌ **Rollback fear** — "let's try one more fix" while users are impacted (rollback first)
- ❌ **Undeclared incidents** — team knows something is wrong but nobody formally declares it

## Reference

- Google SRE Book Ch. 14: Managing Incidents
- Related skills: `post-mortem-templates`, `alerting-strategy`, `runbook-authoring`
- <org> clusters: `<org>-workloads-dev-nv`, `<org>-workloads-prd-nv`, `<org>-eks-prd` (core)
- Alertmanager: `https://alertmanager.<org-domain>`
- Grafana: `https://grafana.<org-domain>`
