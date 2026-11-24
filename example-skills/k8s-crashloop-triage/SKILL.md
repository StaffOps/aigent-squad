---
name: k8s-crashloop-triage
description: How to diagnose pods stuck in CrashLoopBackOff, ImagePullBackOff, ErrImagePull, CreateContainerConfigError or restarting repeatedly, from read-only evidence (status, events, previous logs, probes).
keywords: [crashloopbackoff, crashloop, "crash loop", "crashing pod", "keeps restarting", restarting, restarts, "restart count", imagepullbackoff, errimagepull, "image pull", createcontainerconfigerror, "exit code", "liveness probe", "readiness probe", "probe failed", "back-off restarting"]
---

# Triage: CrashLoopBackOff and restart loops

`CrashLoopBackOff` is not a cause, it is a symptom: the container starts, exits, and
the kubelet waits longer before each restart (exponential back-off up to 5 min).
The job is to find **why the process exits** — and to say which evidence proved it.

Posture: read-only. Collect evidence and explain; any fix is a recommendation for the
workload owner, applied through their normal delivery path (GitOps / CI), never by the agent.

## Step 1 — Read the container state (the most informative single field)

`kubectl get pod <pod> -n <ns> -o yaml` → `status.containerStatuses[]`:

| Field | What it tells you |
|-------|-------------------|
| `state.waiting.reason` | Current symptom (`CrashLoopBackOff`, `ImagePullBackOff`, `CreateContainerConfigError`) |
| `lastState.terminated.reason` | Why the **previous** run ended (`Error`, `OOMKilled`, `Completed`) |
| `lastState.terminated.exitCode` | The process exit code — see table below |
| `lastState.terminated.finishedAt` | When — correlate with deploys, config changes, node events |
| `restartCount` | Rate of failure (compare with pod age) |

Check **all** containers, including init containers (`initContainerStatuses`) and
sidecars — a failing sidecar keeps the whole pod unready.

## Step 2 — Interpret the exit code

| Exit code | Meaning | Usual cause |
|-----------|---------|-------------|
| `0` + reason `Completed` | Process finished normally | A long-running workload whose command exits (wrong entrypoint, job image used as service) |
| `1` / `2` | Application error | Unhandled exception, bad config, missing dependency at startup |
| `126` | Command not executable | Permissions on the binary, wrong architecture image |
| `127` | Command not found | Wrong `command`/`args`, missing binary in a minimal/distroless image |
| `137` | SIGKILL | `OOMKilled` (see the `oomkill-investigation` skill) or liveness probe kill after grace period |
| `139` | SIGSEGV | Native crash, incompatible library, wrong CPU architecture |
| `143` | SIGTERM | Graceful stop — often liveness probe failure or eviction |

`exec format error` in logs means an image built for the wrong architecture (amd64 vs arm64).

## Step 3 — Logs of the crashed run, not the current one

`kubectl logs <pod> -n <ns> -c <container> --previous` shows the run that died.
The current container may have no output yet. Look at the **last lines** before exit:
stack trace, "connection refused", "permission denied", "no such file", config parse errors.

Empty previous logs + exit 137 → killed before it logged (probe or OOM).
Empty logs + exit 127 → the command never started.

## Step 4 — Events

`kubectl describe pod <pod> -n <ns>` (Events section) or
`kubectl get events -n <ns> --field-selector involvedObject.name=<pod>`:

| Event message | Points to |
|---------------|-----------|
| `Liveness probe failed` then `Killing` | Probe too aggressive or the app hangs |
| `Readiness probe failed` (no restarts) | App up but not ready — not a crash loop |
| `Failed to pull image` / `manifest unknown` | Wrong tag, deleted tag, private registry auth |
| `pull access denied` / `401` / `403` | Missing or expired `imagePullSecrets`, registry permissions |
| `no match for platform in manifest` | Image lacks the node's architecture |
| `secret "x" not found` / `configmap "x" not found` | `CreateContainerConfigError` — referenced object missing |
| `couldn't find key x in Secret` | Secret exists but the key is wrong |

## Step 5 — Probes

A healthy app can be killed by a bad probe. Compare the probe with startup reality:

- `initialDelaySeconds` / no `startupProbe` while the app needs longer to boot (JVM, migrations).
- Liveness pointing at an endpoint that checks downstream dependencies — a database blip then
  restarts every replica. Liveness should check the process only.
- `timeoutSeconds: 1` against an endpoint that is slow under load.

## Step 6 — What changed?

Most crash loops start right after a change. Check, with timestamps:
the ReplicaSet revision (`kubectl rollout history deploy/<name> -n <ns>`), image tag,
ConfigMap/Secret updates, and node changes. If only pods on one node fail, suspect the node.

## Common patterns

| Evidence | Likely root cause |
|----------|-------------------|
| Started failing at a new ReplicaSet revision | Regression in the new image or config |
| All replicas fail, old revision also fails | External dependency or shared config/secret changed |
| Only some replicas fail, same node | Node problem (disk, DNS, architecture) |
| Exit 1 + "connection refused" to a dependency | Dependency down; app has no retry at startup |
| Restarts every N minutes, exit 137, no OOM reason | Liveness probe kill |

## Answer format

State the failing container, the exit code and reason, the log line or event that proves
the cause, and the time it started. Mark anything inferred (not seen in evidence) as
unverified. Recommend the fix for the owner (config, probe tuning, image tag, secret key).
