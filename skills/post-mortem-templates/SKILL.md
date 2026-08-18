---
name: post-mortem-templates
description: "Use when writing blameless post-mortems, structuring incident reviews, or tracking action items after incidents. Covers timeline construction, RCA techniques (5 Whys, fishbone), severity-based templates, action item tracking via Jira, and <org>-specific toolchain."
keywords: [post-mortem, post, mortem, "post mortem", incident, rca]
---
# Post-Mortem Templates

Blameless post-mortem framework for <org>. Every SEV1-2 incident requires a post-mortem within 5 business days. SEV3 encouraged.

## Core Principles

1. **Blameless** — focus on systems, processes, and tooling. Never individuals.
2. **Thorough** — timeline must be minute-by-minute during active incident.
3. **Actionable** — every action item has an owner, due date, and Jira ticket.
4. **Shared** — published to team, reviewed in retro, lessons propagated.
5. **Tracked** — action items followed to completion (not filed and forgotten).

## Post-Mortem Structure

### Required Sections

| Section | Purpose |
|---------|---------|
| **Summary** | One paragraph: what happened, duration, impact |
| **Severity** | SEV1/SEV2/SEV3 classification |
| **Timeline** | Minute-by-minute from detection to resolution |
| **Impact** | Users affected, revenue, SLO burn, data loss |
| **Root Cause** | 5 Whys or fishbone analysis |
| **Contributing Factors** | What made it worse (not root cause, but amplifiers) |
| **What Went Well** | Detection speed, response, tooling that helped |
| **What Went Wrong** | Gaps in detection, response, communication |
| **Action Items** | Owner + due date + priority + Jira ticket |
| **Lessons Learned** | Systemic improvements beyond this incident |

## Root Cause Analysis Techniques

### 5 Whys

Start from the symptom, ask "why?" iteratively until reaching a systemic cause:

```
Symptom: API returned 500 errors for 15 minutes
  Why? → Database connection pool exhausted
  Why? → Connections not being released after timeout
  Why? → Missing timeout configuration on new DB client
  Why? → No default timeout in our DB wrapper library
  Why? → Library upgrade changed defaults, not caught in review
  
Root cause: Library upgrade changed connection timeout defaults;
            no integration test validates connection pool behavior under load.
```

Rules:
- Stop when you reach a **systemic** cause (process, tooling, design gap)
- Never stop at "human error" — ask why the system allowed it
- Multiple branches are OK (contributing factors)

### Fishbone (Ishikawa) Diagram

Categories for <org> incidents:

```
                    ┌─── Code ───────── Bug in business logic
                    │                   Missing error handling
                    │
                    ├─── Config ─────── Wrong env var value
                    │                   Missing feature flag
                    │
                    ├─── Infra ──────── Node failure
Incident ───────────┤                   Network partition
                    │                   Disk full
                    │
                    ├─── Deploy ─────── Bad rollout (no canary)
                    │                   Config drift
                    │
                    ├─── Capacity ───── Traffic spike
                    │                   Resource exhaustion
                    │
                    └─── Dependency ─── Upstream service failure
                                        AWS service degradation
                                        Third-party API timeout
```

### Cause Categories

| Category | Examples | Typical Fix |
|----------|----------|-------------|
| **Code Bug** | Null pointer, race condition, logic error | Fix + test + review process |
| **Configuration** | Wrong endpoint, missing env var, bad threshold | Config validation, GitOps review |
| **Infrastructure** | Node crash, disk full, network issue | Autoscaling, monitoring, redundancy |
| **Deployment** | Bad image, missing migration, rollback failure | Canary, progressive delivery, rollback automation |
| **Capacity** | OOM, connection pool, queue backlog | Autoscaling (KEDA), load testing, capacity planning |
| **Dependency** | AWS outage, upstream timeout, cert expiry | Circuit breaker, fallback, monitoring |

## Templates by Severity

### SEV1 Template (Customer-Facing Outage)

```markdown
# Post-Mortem: [TITLE]

**Date**: YYYY-MM-DD
**Severity**: SEV1
**Duration**: HH:MM (from detection to resolution)
**Author**: [Name]
**Reviewers**: [IC, Team Lead, SRE]

## Summary

[One paragraph: what service was affected, what users experienced, duration, resolution]

## Impact

- **Users affected**: [number or percentage]
- **Revenue impact**: [estimated or "under assessment"]
- **SLO burn**: [X% of 30-day error budget consumed]
- **Data loss**: [none / describe]
- **Downstream impact**: [services affected]

## Timeline (UTC-3)

| Time | Event |
|------|-------|
| HH:MM | [First anomaly detected by monitoring] |
| HH:MM | [Alert fired: AlertName → #channel] |
| HH:MM | [IC assigned: Name] |
| HH:MM | [Triage: identified as X] |
| HH:MM | [Mitigation attempted: action] |
| HH:MM | [Mitigation confirmed: metrics recovering] |
| HH:MM | [All clear declared by IC] |
| HH:MM | [Post-mortem initiated] |

## Root Cause Analysis

### 5 Whys

1. Why did [symptom]? → [answer]
2. Why did [answer 1]? → [answer]
3. Why did [answer 2]? → [answer]
4. Why did [answer 3]? → [answer]
5. Why did [answer 4]? → **[systemic root cause]**

### Category: [Code | Config | Infra | Deploy | Capacity | Dependency]

## Contributing Factors

- [Factor 1: what made detection/mitigation slower]
- [Factor 2: what amplified the impact]

## What Went Well

- [Detection: alert fired within X minutes]
- [Response: IC coordinated effectively]
- [Tooling: Grafana dashboard showed clear signal]

## What Went Wrong

- [Gap 1: no runbook for this scenario]
- [Gap 2: rollback took too long because X]
- [Gap 3: communication delay to stakeholders]

## Action Items

| # | Action | Owner | Priority | Due Date | Jira |
|---|--------|-------|----------|----------|------|
| 1 | [Fix root cause] | [Name] | P1 | YYYY-MM-DD | PROJ-XXX |
| 2 | [Add monitoring for X] | [Name] | P1 | YYYY-MM-DD | PROJ-XXX |
| 3 | [Write runbook for scenario] | [Name] | P2 | YYYY-MM-DD | PROJ-XXX |
| 4 | [Add integration test] | [Name] | P2 | YYYY-MM-DD | PROJ-XXX |
| 5 | [Improve rollback automation] | [Name] | P3 | YYYY-MM-DD | PROJ-XXX |

## Lessons Learned

- [Systemic improvement 1]
- [Systemic improvement 2]
- [Process change recommendation]
```

### SEV2 Template (Degraded Service)

Same structure as SEV1 but:
- Revenue impact often "minimal" or "none"
- Timeline can be less granular (5-min intervals OK)
- Fewer mandatory reviewers (team lead + SRE)
- Action items: P2-P3 priority typical

### SEV3 Template (Internal Impact)

Simplified:

```markdown
# Post-Mortem: [TITLE]

**Date**: YYYY-MM-DD | **Severity**: SEV3 | **Duration**: HH:MM

## Summary
[2-3 sentences]

## Root Cause
[Category]: [Brief description]

## Action Items
| Action | Owner | Due | Jira |
|--------|-------|-----|------|
| [Fix] | [Name] | YYYY-MM-DD | PROJ-XXX |

## Lessons Learned
- [Key takeaway]
```

## Action Item Standards

### Requirements

Every action item MUST have:
- **Specific description** (not "improve monitoring" → "add alert for connection pool > 80%")
- **Single owner** (one person accountable, not "the team")
- **Due date** (realistic, within 2 sprints for P1-P2)
- **Jira ticket** (tracked in component `post-mortem`)
- **Priority**: P1 (this sprint), P2 (next sprint), P3 (backlog)

### Priority Guidelines

| Priority | Timeline | Criteria |
|----------|----------|----------|
| **P1** | This sprint (1-2 weeks) | Prevents recurrence of same incident |
| **P2** | Next sprint (2-4 weeks) | Improves detection or reduces blast radius |
| **P3** | Backlog (1-2 months) | General improvement, nice-to-have |

### Tracking

- Jira component: `post-mortem`
- Label: `postmortem-action`
- Review: weekly in SRE standup until all P1 items closed
- Stale check: items open > 30 days past due → escalate to team lead

## <org> Toolchain

| Tool | Purpose | Location |
|------|---------|----------|
| **Confluence** | Post-mortem document storage | Space: `SRE/Post-Mortems/YYYY/` |
| **Jira** | Action item tracking | Component: `post-mortem`, Label: `postmortem-action` |
| **Slack** | Async review + sharing | `#postmortem` channel |
| **Grafana** | Evidence (dashboard screenshots, links) | `https://grafana.<org-domain>` |
| **Alertmanager** | Alert history | `https://alertmanager.<org-domain>` |
| **GitLab** | Code changes (MRs linked in timeline) | Internal GitLab |

### Process Flow

```
Incident resolved
  → IC creates post-mortem doc (within 48h)
  → Author fills template (within 5 business days)
  → Review meeting (team + SRE, 30-60 min)
  → Action items created in Jira
  → Published to #postmortem Slack channel
  → Discussed in team retro (next sprint)
  → Action items tracked to completion
```

### Review Meeting Format (30-60 min)

1. **Read-through** (5 min) — everyone reads silently
2. **Timeline clarification** (10 min) — fill gaps, correct sequence
3. **RCA discussion** (15 min) — validate root cause, add contributing factors
4. **Action items** (15 min) — assign owners, set priorities, create Jira tickets
5. **Lessons** (5 min) — what to share broadly

### Sharing Rules

- All post-mortems shared in `#postmortem` Slack channel
- Customer names anonymized in shared docs (use "Customer A", "Customer B")
- Internal metrics (revenue, user counts) OK within <org>
- External sharing (blog, conference): requires management approval

## Blameless Culture

### Language Guide

| ❌ Avoid | ✅ Use Instead |
|----------|---------------|
| "John broke production" | "The deployment introduced a regression" |
| "The team was careless" | "The review process didn't catch the issue" |
| "Should have known better" | "The system didn't surface this risk" |
| "Human error" | "The interface/process allowed an unsafe action" |
| "Fault of team X" | "The handoff between teams lacked validation" |

### Principles

- **Systems fail, not people** — if a human can make a mistake, the system should prevent it
- **Transparency over comfort** — hiding details prevents learning
- **Forward-looking** — "how do we prevent this?" not "who caused this?"
- **Psychological safety** — people must feel safe reporting without fear of punishment

## Anti-patterns

- ❌ **Blame** — naming individuals as the cause ("John deployed the bad code")
- ❌ **Vague action items** — "improve monitoring" (what specifically? by when? who?)
- ❌ **No follow-up** — action items created but never tracked to completion
- ❌ **Delayed post-mortem** — writing it 3 weeks later (memory fades, details lost)
- ❌ **Copy-paste timeline** — just pasting Slack messages without analysis
- ❌ **Missing impact quantification** — "some users were affected" (how many? how long?)
- ❌ **Root cause = "human error"** — always dig deeper into systemic causes
- ❌ **No review meeting** — document filed without discussion (misses context)
- ❌ **Action items without Jira** — untracked items never get done
- ❌ **Skipping SEV2 post-mortems** — "it wasn't that bad" (patterns emerge from SEV2s)

## Reference

- Google SRE Book Ch. 15: Postmortem Culture
- Related skills: `incident-response-runbook`, `alerting-strategy`, `sla-slo-design`
- <org> Slack: `#postmortem` (sharing), `#eks-notifications-*` (alert history)
- Grafana: `https://grafana.<org-domain>` (evidence dashboards)
