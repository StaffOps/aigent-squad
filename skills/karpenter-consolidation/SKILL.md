---
name: karpenter-consolidation
description: "Karpenter node consolidation patterns. Use when investigating why nodes aren't consolidating, debugging disruption blocks, optimizing instance types, or reducing node count for cost savings."
keywords: [karpenter-consolidation, karpenter, consolidation, "karpenter consolidation", cost, node, savings]
---
# Karpenter Consolidation Patterns

How Karpenter decides to consolidate nodes, common blockers, and how to force better packing.

## How consolidation works

With `consolidationPolicy: WhenEmptyOrUnderutilized`:
1. Karpenter evaluates each node: can its pods fit on other existing nodes OR a cheaper replacement node?
2. Waits `consolidateAfter` (e.g., 15m) before acting
3. Respects `budgets.nodes` (e.g., 10% = max 1-2 nodes disrupted at once)
4. Respects PodDisruptionBudgets
5. Only consolidates if the result is **cheaper**

## Common blockers

### PDB prevents eviction
```
DisruptionBlocked: Pdb prevents pod evictions (PodDisruptionBudget=[monitoring/my-service])
```
**Cause**: Service has `maxUnavailable: 0` or already at minimum healthy pods.
**Fix**: Review PDB — ensure `maxUnavailable >= 1` or increase replicas.

### Can't replace with a cheaper node
```
Unconsolidatable: Can't replace with a cheaper node
```
**Cause**: Pod fills most of the node; no smaller instance type fits it. Karpenter won't move to a BIGGER node because it's more expensive per-node (even if fewer total nodes).
**Fix**: Either reduce pod requests or restrict instance types to force larger nodes (where multiple pods pack together).

### SpotToSpotConsolidation threshold
```
SpotToSpotConsolidation requires 15 cheaper instance type options than the current candidate to consolidate, got N
```
**Cause**: Karpenter requires 15 alternative cheaper instance types available for spot-to-spot consolidation (ensures capacity diversity). Restrictive NodePool requirements (arch, family, size) reduce available options below 15.
**Fix**: Broaden instance families/categories in NodePool requirements, or accept that spot nodes won't consolidate to spot (they may consolidate to on-demand if cheaper).

### Node nominated for pending pod
```
DisruptionBlocked: Node is nominated for a pending pod
```
**Cause**: A pod is about to be scheduled on this node. Temporary — resolves in seconds.

## Investigation commands

```bash
# Why aren't nodes consolidating?
kubectl get events -A | grep -i "Unconsolidatable\|DisruptionBlocked"

# NodePool config
kubectl get nodepool <name> -o json | jq '.spec.disruption'

# What's preventing eviction on a specific node?
kubectl get events --field-selector involvedObject.name=<node-name> | grep -i disruption

# PDBs in namespace
kubectl get pdb -n <ns> -o custom-columns="NAME:.metadata.name,MIN-AVAIL:.spec.minAvailable,MAX-UNAVAIL:.spec.maxUnavailable,ALLOWED:.status.disruptionsAllowed"
```

## Forcing consolidation to larger nodes

### Problem
Karpenter optimizes per-pod (cheapest node that fits each pod). It doesn't natively optimize "fewer larger nodes" unless that's cheaper per-pod.

### Solution: Restrict instance size minimum
```yaml
spec:
  template:
    spec:
      requirements:
        - key: karpenter.k8s.aws/instance-size
          operator: NotIn
          values: ["nano", "micro", "small", "medium", "large", "xlarge"]
```
This forces minimum `2xlarge`, enabling multiple pods per node.

### Alternative: Separate NodePool for large workloads
Create a NodePool specifically for vmstorage/vmselect with instance requirements that force co-location.

## SecurityGroupDrift — mass node replacement

**Symptom**: All nodes in a NodePool being replaced simultaneously.

**Cause**: A Security Group gained/lost the tag that the `securityGroupSelectorTerms` matches. Karpenter detects SG mismatch → marks all nodes as Drifted → tries to replace all.

**Investigation**:
```bash
# Find drift reason
kubectl get nodeclaim -o json | jq '.items[] | select(.status.conditions[] | select(.type=="Drifted" and .status=="True")) | {name: .metadata.name, reason: (.status.conditions[] | select(.type=="Drifted") | .reason)}'

# Check current SGs resolved by the EC2NodeClass
kubectl get ec2nodeclass <name> -o json | jq '{securityGroupSelector: .spec.securityGroupSelectorTerms, currentSGs: .status.securityGroups}'
```

**Prevention**: Use specific SG IDs instead of tag selectors when possible, or ensure tags are managed exclusively by one team/tool.

## Metrics for monitoring consolidation

| Metric | Purpose |
|--------|--------|
| `karpenter_voluntary_disruption_decisions_total{decision="blocked"}` | Consolidation attempts blocked |
| `karpenter_voluntary_disruption_eligible_nodes` | Nodes eligible for disruption |
| `karpenter_nodeclaims_disrupted_total` | Nodes actually disrupted |
| `karpenter_voluntary_disruption_consolidation_timeouts_total` | Algorithm timeouts |
| `karpenter_nodes_current_lifetime_seconds` | Node age distribution |

## Reference

- Karpenter docs: https://karpenter.sh/docs/concepts/disruption/
- Metrics: https://karpenter.sh/docs/reference/metrics/
- Instance types: https://karpenter.sh/docs/reference/instance-types/
