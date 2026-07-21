---
name: kyverno-policies
description: "Kyverno policies enforced at <ORG>. Use when debugging pod admission failures, understanding mandatory labels, image mutation rules, or designing new policies. Covers ClusterPolicy patterns, <ORG>-specific rules, exceptions, and troubleshooting."
keywords: [kyverno-policies, kyverno, policies, "kyverno policies"]
---
# Kyverno Policies at <ORG>

## Overview

Kyverno is the policy engine enforcing infrastructure and security standards at admission time across all <ORG> EKS clusters. It runs in the `kyverno` namespace and intercepts every resource creation/update via admission webhooks.

## Policy types

| Type | Scope | Use case |
|------|-------|----------|
| `ClusterPolicy` | Cluster-wide | Mandatory labels, image mutation, security baselines |
| `Policy` | Namespace-scoped | Namespace-specific exceptions or additional rules |

<ORG> uses `ClusterPolicy` for all standard enforcement. Namespace-scoped `Policy` is rare.

## <ORG> mandatory policies

### 1. Mandatory labels (validate)

All pods MUST carry these labels. Without them, FinOps reporting breaks and OTel enrichment fails.

```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: require-mandatory-labels
spec:
  validationFailureAction: Enforce
  background: true
  rules:
    - name: check-labels
      match:
        any:
          - resources:
              kinds:
                - Pod
      exclude:
        any:
          - resources:
              namespaces:
                - kube-system
                - istio-system
                - monitoring
                - kyverno
                - cert-manager
      validate:
        message: >-
          Pod must have labels: CostCenter, Environment,
          app.kubernetes.io/name, app.kubernetes.io/version
        pattern:
          metadata:
            labels:
              CostCenter: "?*"
              Environment: "?*"
              app.kubernetes.io/name: "?*"
              app.kubernetes.io/version: "?*"
```

**Impact of missing `CostCenter`**: the `k8sattributesprocessor` in OTel Collector filters pods without this label — telemetry will NOT be enriched with pod metadata.

### 2. Image mutation (mutate)

ALL container image references are rewritten to route through Harbor proxy. This ensures vulnerability scanning, caching, and availability even if upstream registries are down.

```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: mutate-images-to-harbor
spec:
  rules:
    - name: rewrite-image-registry
      match:
        any:
          - resources:
              kinds:
                - Pod
      exclude:
        any:
          - resources:
              namespaces:
                - kube-system
      mutate:
        foreach:
          - list: "request.object.spec.containers"
            patchStrategicMerge:
              spec:
                containers:
                  - name: "{{ element.name }}"
                    image: "harbor.<org>.com/proxy-cache/{{ regex_replace_all('^[^/]+/', element.image, '') }}"
```

**You do NOT need to manually prefix images** — Kyverno handles it transparently.

### 3. Resource requests (validate)

Every container must declare `resources.requests` for proper scheduling and ScaleOps optimization.

### 4. Pod security (validate)

Enforces restricted security context on all workload pods:
- `runAsNonRoot: true`
- `readOnlyRootFilesystem: true`
- `allowPrivilegeEscalation: false`
- `capabilities.drop: ["ALL"]`

### 5. cosign image verification (verifyImages)

Production images must be signed with cosign. Unsigned images are rejected in PRD/HML/BTC.

```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: verify-image-signature
spec:
  validationFailureAction: Enforce
  rules:
    - name: verify-cosign
      match:
        any:
          - resources:
              kinds:
                - Pod
              namespaces:
                - "prd-*"
                - "hml-*"
                - "btc-*"
      verifyImages:
        - imageReferences:
            - "harbor.<org>.com/<org>-images/*"
          attestors:
            - entries:
                - keys:
                    publicKeys: |-
                      -----BEGIN PUBLIC KEY-----
                      <cosign public key from cosign.pub>
                      -----END PUBLIC KEY-----
```

**DEV** uses `Audit` mode (warn only) for faster iteration.

## Enforcement modes

| Mode | Behavior | Use case |
|------|----------|----------|
| `Enforce` | Block non-compliant resources | PRD/HML/BTC |
| `Audit` | Allow but report violations | DEV, new policy rollout |

Always roll out new policies in `Audit` mode first, review reports, then switch to `Enforce`.

## Excluded namespaces

System namespaces are excluded from most policies to avoid breaking cluster components:

- `kube-system` — core K8s components
- `istio-system` — mesh control plane
- `monitoring` — observability stack
- `kyverno` — policy engine itself
- `cert-manager` — certificate management
- `external-secrets` — secrets operator

## Debugging admission failures

### Step 1: Check pod events

```bash
kubectl describe pod <pod-name> -n <namespace>
# Look for "admission webhook" errors in Events
```

### Step 2: Check policy reports

```bash
kubectl get policyreport -n <namespace>
kubectl describe policyreport -n <namespace>

kubectl get clusterpolicyreport
```

### Step 3: Identify which policy blocked

```bash
kubectl get events -n <namespace> --field-selector reason=PolicyViolation
```

### Step 4: Common fixes

| Error | Fix |
|-------|-----|
| Missing `CostCenter` label | Add to pod template labels in Helm values |
| Missing `resources.requests` | Add cpu/memory requests to container spec |
| Image not signed | Run cosign sign in CI pipeline |
| Security context violation | Add `securityContext` block to pod spec |

## Designing new policies

1. Write the `ClusterPolicy` with `validationFailureAction: Audit`
2. Deploy to DEV cluster
3. Monitor `PolicyReport` for unexpected violations
4. Fix false positives (add exclusions)
5. Switch to `Enforce` after validation period
6. Deploy to PRD

## Anti-patterns

- ❌ Deploying policies in `Enforce` mode without testing in `Audit` first
- ❌ Policies without `exclude` for system namespaces (breaks cluster components)
- ❌ Overly broad `match` rules that catch Jobs, CronJobs, or system pods
- ❌ Hardcoding image registries in application manifests (let Kyverno mutate)
- ❌ Disabling Kyverno webhooks to "fix" deployment issues (fix the root cause)
- ❌ Using namespace-scoped `Policy` for rules that should be cluster-wide
- ❌ Ignoring `PolicyReport` violations in Audit mode ("it's just a warning")
- ❌ Creating exceptions without documenting why

## Related

- `cosign-image-signing` skill — signing details, key rotation, Harbor `--new-bundle-format=false` gotcha (Kyverno verifies signatures created by this)
- `cosign-signing-mandatory` steering — image signing requirements
- `k8s-best-practices-<org>` steering — mandatory labels and security context
- `aws-tag-policies` steering — CostCenter values reference
- `helm-chart-app` skill — how labels are set in Helm values
