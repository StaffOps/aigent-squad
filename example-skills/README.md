# Example skills

These are **generic examples** of skill definitions, shipped with the product so
it works out of the box and so you can see the format. They are not meant to be
a complete knowledge catalog: your own skills (runbooks, tool catalogs, team
conventions) belong in your environment, not in this repository.

## Using your own skills

The registry loads every `<name>/SKILL.md` one level below `SKILLS_DIR`
(default `example-skills`; the image sets `SKILLS_DIR=/app/example-skills`).

1. Keep your catalog in your own repo (one directory per skill, `SKILL.md` inside).
2. Mount it into the supervisor and point `SKILLS_DIR` at it, e.g. a ConfigMap or
   a volume populated by your GitOps tooling, with `SKILLS_DIR=/app/my-skills`.
3. Allowlist the skills per agent in `agents/<agent>/agent.yaml` (`skills:`).
   An allowlisted name with no skill directory is ignored.

## Format

```markdown
---
name: k8s-crashloop-triage            # must match the directory name
description: One sentence on when to use it.
keywords: [crashloopbackoff, "crash loop", cert-manager]
---

# Body: the knowledge injected into the agent's prompt
```

- A skill is injected only when the query matches one of its `keywords`
  (single words on word boundaries, phrases as substrings, punctuated keywords
  such as `cert-manager` on a contiguous token run).
- At most 3 skills are injected per turn, in allowlist order — list the most
  specific skills first.
- Keep skills read-only in posture: they explain how to diagnose; they never
  instruct the agent to change anything.

## The examples

| Skill | Agent(s) |
|-------|----------|
| `k8s-crashloop-triage`, `k8s-pending-pod-scheduling`, `k8s-node-pressure-triage`, `oomkill-investigation` | kubernetes |
| `metric-discovery-before-query` | observability |
| `aws-idle-resource-inventory` | aws, finops |
| `aws-security-group-audit`, `iam-least-privilege-review` | aws, security |
| `guardduty-finding-triage` | security |
| `cost-spike-explanation` | finops |
| `ci-pipeline-failure-triage` | devops |

Details: [`docs/HOW-TO-NEW-AGENT.md`](../docs/HOW-TO-NEW-AGENT.md#skills-lazy-loaded-knowledge),
design in [`specs/26-agent-skills/`](../specs/26-agent-skills/).
