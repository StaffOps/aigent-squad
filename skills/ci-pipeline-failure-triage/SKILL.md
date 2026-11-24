---
name: ci-pipeline-failure-triage
description: How to diagnose a failed or slow CI/CD pipeline or deployment job read-only — find the first failing job, classify the failure (code, test flake, dependency, infrastructure, credentials, quota), and correlate a production problem with the most recent deployment.
keywords: ["pipeline failed", "pipeline failing", "failed pipeline", "build failed", "build failing", "job failed", "failed job", "ci failed", "ci failing", "deploy failed", "deployment failed", "flaky test", "flaky", "pipeline stuck", "job stuck", "runner", "pending job", "slow pipeline", "what changed", "recent deploy", "last deploy", "caused by deploy", "broke after"]
---

# CI/CD pipeline and deployment failure triage

Pipelines fail for a small set of reasons. Find the **first** failing job (later failures
are usually consequences), classify it, and quote the log line that proves the class.

Posture: read-only. Read pipeline/job status and logs through the CI system's API.
Never retry, cancel, re-run or trigger pipelines, and never edit variables or settings —
recommend the action to the owner.

## Step 1 — Locate the first failure

- Get the pipeline's jobs ordered by stage; the earliest failed job is the one that matters.
- Note whether it is `failed`, `canceled`, `skipped`, or `allowed to fail` — only the first
  blocks; skipped jobs downstream are a consequence.
- Record the commit SHA, branch/MR, who triggered it, and when it last passed on the same branch.

## Step 2 — Read the tail of the job log

The cause is usually in the last 50–100 lines; search upward for the first `error`,
`FAILED`, `fatal`, `exit code`. Classify:

| Class | Typical log evidence | Next check |
|-------|----------------------|-----------|
| Code / compile | Compiler, type or lint errors naming a file and line | The diff of the triggering commit |
| Test failure | Assertion errors, named failing tests | Does it fail on the previous commit too? |
| Flaky test | Same test passes on retry or fails intermittently across unrelated commits | Failure history of that test |
| Dependency | `404` / `not found` / checksum mismatch for a package or image; registry rate limits (`429`, "toomanyrequests") | Pinned versions, upstream availability, lockfile changes |
| Credentials | `401`, `403`, "permission denied", "token expired", "could not assume role" | Expiry of CI tokens, OIDC trust conditions, rotated secrets |
| Infrastructure | Runner lost, `no space left on device`, OOM kill, timeouts with no output | Runner health, disk, concurrency |
| Quota / limits | Cloud API throttling, account limits, CI minutes exhausted | Provider limits |
| Deploy step | Helm/Kubernetes errors (`ImagePullBackOff`, failed hooks, rollout timeout), Terraform plan/apply errors | Target environment state; see Kubernetes triage skills |

## Step 3 — Pending or stuck jobs

A job that never starts is not a code problem:
- No runner with matching tags/labels is online.
- Concurrency limits or a queue of earlier pipelines.
- A protected environment waiting for manual approval.
- A required upstream pipeline or resource group lock.

## Step 4 — Slow pipelines

Compare job durations with recent successful runs. Usual causes: cache misses (dependency
or Docker layer cache invalidated), larger test suites, runner contention, network pulls
of large images, multi-arch builds under emulation.

## Step 5 — Did a deployment cause a production problem?

When the question is "did the last deploy break X":

1. List deployments to the affected environment with timestamps (CI deploy jobs, GitOps
   sync history, Kubernetes ReplicaSet revisions).
2. Align the symptom start time (from metrics/alerts) with the deploy time. Causation needs
   the deploy to **precede** the symptom, and the affected service to be the one deployed
   or a direct dependency.
3. Diff what changed: image tag, config/values, feature flags, infrastructure.
4. Check counter-evidence: did the symptom also appear in environments without the deploy,
   or start before it?

A tight time correlation is strong but not proof; say how strong the link is.

## Answer format

The failing job and stage, the failure class, the quoted log line, when it last passed and
what changed since, and the recommended owner action (fix code, quarantine flaky test, pin
dependency, renew credential, fix runner, roll back via the normal delivery path).
Separate verified findings from inferences.
