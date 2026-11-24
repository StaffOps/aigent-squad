---
name: k8s-node-pressure-triage
description: How to diagnose NotReady nodes, MemoryPressure/DiskPressure/PIDPressure conditions and kubelet evictions, and tell node-level problems apart from single-workload problems, using read-only evidence.
keywords: [notready, "not ready", "node not ready", "node down", memorypressure, diskpressure, pidpressure, "memory pressure", "disk pressure", "pid pressure", evicted, eviction, evictions, "node pressure", "node unhealthy", "kubelet stopped posting", "ephemeral storage", "node condition"]
---

# Triage: node NotReady, pressure conditions and evictions

Node problems show up as many unrelated pods failing on **the same node**. Confirm that
grouping first; a single failing workload is rarely a node problem.

Posture: read-only. Do not cordon, drain or reboot — explain the condition and recommend
the action to the platform owner.

## Step 1 — Node conditions

`kubectl describe node <node>` → Conditions:

| Condition | True means | Kubelet reaction |
|-----------|------------|------------------|
| `Ready=False` | Kubelet reports unhealthy | Pods not scheduled; after timeout, pods are evicted |
| `Ready=Unknown` | Kubelet stopped heartbeating ("Kubelet stopped posting node status") | Node lost: network, kubelet crash, instance stopped |
| `MemoryPressure` | Available memory below eviction threshold | Evicts pods, `BestEffort` first, then `Burstable` above request |
| `DiskPressure` | Root or image filesystem low on space/inodes | Garbage-collects images, then evicts pods using ephemeral storage |
| `PIDPressure` | Process IDs near limit | Evicts pods; often a fork bomb or thread leak |
| `NetworkUnavailable` | CNI not configured | No pod networking on that node |

Read `lastTransitionTime` and the `message` of each condition — it gives the threshold hit.

## Step 2 — Group the evidence by node

- `kubectl get pods -A -o wide --field-selector spec.nodeName=<node>` — which pods run there.
- `kubectl get events -A --field-selector involvedObject.kind=Node,involvedObject.name=<node>`.
- Evicted pods keep a `status.reason: Evicted` and a message naming the resource
  (`The node was low on resource: memory` / `ephemeral-storage`).

If evictions cluster on one node and its condition flipped at the same time, it is a node
problem. If they span many nodes at once, suspect a cluster-wide cause (a DaemonSet,
a log-volume burst, a control-plane or network event).

## Step 3 — Per-condition causes

### MemoryPressure
- Pods with no memory **limits** (or limits much higher than requests) overcommitting the node.
- A memory leak in one pod pushing the whole node — find the top consumer by working set.
- System daemons without reserved memory (`kube-reserved` / `system-reserved` not set).

### DiskPressure
- Container logs not rotated, or very chatty applications.
- Pods writing to `emptyDir` or the container filesystem without `ephemeral-storage` limits.
- Many large images on small root volumes; image garbage collection cannot keep up.
- Inode exhaustion (many small files) — message says `inodes`, not bytes.

### PIDPressure
- An application spawning processes/threads without bound; check per-pod PID usage.

### NotReady / Unknown
- Instance stopped, terminated or impaired at the cloud level (check instance status checks).
- Kubelet or container runtime crashed or restarting (node-level logs: kubelet, containerd).
- Network partition between node and API server; security group or route change.
- Node hit hard resource exhaustion and the kubelet itself was starved.
- Spot / preemptible interruption or autoscaler scale-down in progress.

## Step 4 — Metrics to confirm (if a metrics backend is available)

Discover names first (see `metric-discovery-before-query`). Typical signals:
node available memory, filesystem available bytes/inodes on the root and image filesystems,
`kube_node_status_condition` (kube-state-metrics), kubelet eviction counters, and per-pod
working set / ephemeral storage on the affected node. Look at the **trend into** the event,
not only the current value.

## Answer format

Name the node, the condition and its transition time, the threshold message, the top
contributing pods with numbers, and whether the issue is node-local or cluster-wide.
Recommend the owner action (set limits, add ephemeral-storage limits, log rotation, larger
root volume, reserved resources, replace the instance). Flag inferences as unverified.
