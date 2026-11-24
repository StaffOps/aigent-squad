---
name: k8s-pending-pod-scheduling
description: How to explain why a pod stays Pending — FailedScheduling reasons (insufficient cpu/memory, taints, affinity, topology spread, PVC binding, quotas) and node-autoscaler behaviour, from read-only evidence.
keywords: [pending, "stuck pending", "pod pending", failedscheduling, unschedulable, "insufficient cpu", "insufficient memory", "didn't match", taint, taints, toleration, "node affinity", "pod anti-affinity", "topology spread", "unbound immediate persistentvolumeclaims", "resource quota", "exceeded quota", "not scheduling", "can't schedule", "cannot schedule"]
---

# Triage: pods stuck in Pending

A `Pending` pod has been accepted by the API but either **no node was chosen**
(scheduling) or the node has not started it yet (image pull, volume attach).
Read the scheduler's own explanation before guessing.

Posture: read-only. Explain the blocking constraint and recommend changes to the owner.

## Step 1 — Which kind of Pending?

`kubectl get pod <pod> -n <ns> -o wide`:

- `NODE` empty → scheduling problem (continue to Step 2).
- `NODE` set, status still `Pending` / `ContainerCreating` → node-side: image pull, volume
  attach/mount, CNI IP allocation. Read events for `FailedMount`, `FailedAttachVolume`,
  `FailedCreatePodSandBox`.

## Step 2 — Read the FailedScheduling event verbatim

`kubectl describe pod <pod> -n <ns>` → Events. The scheduler prints one line summarising
every node, e.g.:

```
0/12 nodes are available: 3 Insufficient memory, 4 node(s) had untolerated taint
{dedicated: gpu}, 5 node(s) didn't match Pod's node affinity/selector.
```

The numbers add up to the node count. Each clause is an independent reason, so fixing one
may not be enough. Quote this line in the answer.

## Step 3 — Map each reason to its cause

| Message fragment | Meaning | What to check |
|------------------|---------|---------------|
| `Insufficient cpu` / `Insufficient memory` | Sum of **requests** on the node leaves no room | Pod requests vs node allocatable; requests are what count, not real usage |
| `had untolerated taint {k: v}` | Node is reserved | Pod `tolerations` vs `kubectl get nodes -o custom-columns=N:.metadata.name,T:.spec.taints` |
| `didn't match Pod's node affinity/selector` | `nodeSelector` / required affinity names labels no node has | Compare with `kubectl get nodes --show-labels` |
| `didn't match pod anti-affinity rules` | Required anti-affinity: one replica per node/zone | Replicas > eligible nodes/zones means a hard ceiling |
| `didn't match pod topology spread constraints` | `whenUnsatisfiable: DoNotSchedule` with `maxSkew` exceeded | Zones/nodes available vs replicas |
| `node(s) had volume node affinity conflict` | PV is bound to another zone | Zonal EBS-style volume; pod must run in that zone |
| `pod has unbound immediate PersistentVolumeClaims` | PVC not bound | `kubectl get pvc -n <ns>`; StorageClass exists, provisioner healthy, `WaitForFirstConsumer` vs `Immediate` |
| `Too many pods` | Node pod limit reached | Max pods per node (CNI IP limits on some clouds) |
| `node(s) were unschedulable` | Nodes cordoned | Maintenance or drain in progress |

If there is **no** FailedScheduling event: check `ResourceQuota` (the pod may never be
created — look at the ReplicaSet events for `exceeded quota`) and `LimitRange` defaults.
`kubectl describe quota -n <ns>` shows used vs hard.

## Step 4 — Is the autoscaler going to fix it?

With a node autoscaler (Cluster Autoscaler, Karpenter or a managed equivalent), Pending
pods should trigger new nodes. If they do not, the reason is usually one of:

- The pod's constraints match **no** node group / node pool (wrong instance family, zone,
  architecture or a taint no pool tolerates).
- Node group / pool is at its max size or a limit (CPU/memory cap) is reached.
- Cloud capacity or quota errors (insufficient capacity in the zone, account vCPU limit).
- Required anti-affinity: a new node does not help when the ceiling is the zone count.

Look for autoscaler events on the pod (`TriggeredScaleUp`, `NotTriggerScaleUp`) and its
controller logs; quote the reason it gives.

## Step 5 — Requests sanity

Pending due to `Insufficient` often means requests are set far above real usage. Compare
`resources.requests` with observed usage (`kubectl top pod`, or container CPU/memory
metrics over a few days). Large requests on a single pod can also exceed **any** node's
allocatable — then no amount of scale-up helps.

## Answer format

Quote the scheduler line, name the constraint(s) that block, give the numbers (requests vs
allocatable, replicas vs zones), say whether the autoscaler can resolve it, and recommend
the owner-side change (lower requests, add toleration, relax affinity to preferred, fix
StorageClass). Mark inferences as unverified.
