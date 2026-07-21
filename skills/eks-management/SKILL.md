---
name: eks-management
description: "AWS EKS cluster management patterns. Use when troubleshooting node provisioning, designing node groups, working with Karpenter, debugging IRSA, or managing cluster upgrades. Covers <org> clusters layout, Karpenter usage, IAM Roles for Service Accounts, common failure modes."
keywords: [eks, iam, karpenter, node, aws, irsa]
---
# AWS EKS Management

Patterns and operations for EKS clusters at <org>.

## <org> EKS clusters

| Context | Cluster | Region | Purpose |
|---------|---------|--------|---------|
| `dev` | <org>-eks-dev | us-east-1 | Development |
| `prd-nv` | <org>-eks-prd-nv | us-east-1 | Production (NV) |
| `core-devops` | <org>-eks-core | us-east-1 | Observability backends |
| (full ARN) | <org>-eks-prd | us-east-1 | Production |

All in `us-east-1`. Account ID for prd: `<ACCOUNT_ID>`.

## EKS managed components

EKS provides managed control plane:
- **API Server**: managed (HA across AZs)
- **etcd**: managed (HA, encrypted)
- **Scheduler**: managed
- **Controller Manager**: managed

You're responsible for:
- Worker nodes
- Add-ons (CNI, EBS CSI, kube-proxy in some cases)
- Authentication / authorization (RBAC, IAM mappings)
- Networking (VPC, security groups, ingress)

## Node provisioning — Karpenter (preferred at <org>)

Karpenter is <org>'s standard for node autoscaling (vs EKS managed node groups).

### Why Karpenter
- Faster pod scheduling (provisions exact node type needed)
- Better cost efficiency (right-sized instances)
- Spot integration with consolidation
- No need to manage node group sizes manually

### Karpenter resources

```yaml
apiVersion: karpenter.sh/v1
kind: NodePool
metadata:
  name: default
spec:
  template:
    spec:
      requirements:
        - key: kubernetes.io/arch
          operator: In
          values: [amd64, arm64]
        - key: karpenter.sh/capacity-type
          operator: In
          values: [spot, on-demand]
        - key: kubernetes.io/instance-type
          operator: In
          values: [m6i.large, m6i.xlarge, m7g.large, m7g.xlarge]
      nodeClassRef:
        group: karpenter.k8s.aws
        kind: EC2NodeClass
        name: default
  limits:
    cpu: 1000
    memory: 1000Gi
  disruption:
    consolidationPolicy: WhenEmptyOrUnderutilized
    consolidateAfter: 30s
```

### Karpenter labels emitted on nodes

| Label | Example | Notes |
|-------|---------|-------|
| `karpenter.sh/capacity-type` | `spot`, `on-demand` | Used for scheduling spot tolerance |
| `karpenter.sh/nodepool` | `default` | NodePool that provisioned the node |
| `karpenter.k8s.aws/instance-family` | `m6i` | Instance family |
| `topology.kubernetes.io/zone` | `us-east-1a` | AZ |
| `kubernetes.io/arch` | `amd64`, `arm64` | Architecture (Graviton on arm64) |

### Common Karpenter operations

```bash
# List nodes provisioned by Karpenter
kubectl get nodes -l karpenter.sh/nodepool

# Show NodePool status
kubectl get nodepool

# Show NodeClaim (pending node provision)
kubectl get nodeclaim

# Force node consolidation
kubectl annotate node <node> karpenter.sh/disruption=delete

# Karpenter logs
kubectl logs -n karpenter -l app.kubernetes.io/name=karpenter --tail=100
```

### Karpenter ephemeral node issue

Karpenter creates/destroys nodes constantly. This breaks systems that cache node lists:
- prometheus-operator endpoint discovery (broken — see `kubelet-scrape-architecture` skill)
- Velero node-agent (must use `nodeAffinity` carefully)
- Local volume provisioner (avoid — use EBS)

Solution: use Kubernetes `kubernetes_sd_configs: role: node` for discovery (auto-syncs).

## IRSA (IAM Roles for Service Accounts)

Pattern for granting AWS permissions to pods without static credentials.

### Setup (per cluster)

1. Enable OIDC provider (one-time):
```bash
eksctl utils associate-iam-oidc-provider --cluster <org>-eks-dev --approve
```

2. Create IAM role with trust policy referencing the OIDC provider + service account

3. Annotate the service account:
```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: my-app
  namespace: my-namespace
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::<ACCOUNT_ID>:role/my-app-role
```

### Validation

From inside a pod:
```bash
kubectl exec -it <pod> -- aws sts get-caller-identity
# Should show: arn:aws:sts::<ACCOUNT_ID>:assumed-role/my-app-role/...
```

### IRSA failure modes

| Symptom | Cause |
|---------|-------|
| `WebIdentityErr: failed to retrieve credentials` | OIDC provider not configured |
| `An error occurred (AccessDenied) when calling AssumeRole` | Trust policy incorrect |
| Old credentials being used | Pod started before annotation was added — restart pod |

## EKS add-ons

Common add-ons at <org>:
- VPC CNI (managed)
- CoreDNS (managed)
- kube-proxy (managed) — but ambient mode disables most kube-proxy use
- EBS CSI Driver (managed)
- AWS Load Balancer Controller (helm)

```bash
aws eks list-addons --cluster-name <org>-eks-dev
aws eks describe-addon --cluster-name <org>-eks-dev --addon-name vpc-cni
```

## Cluster upgrades

Standard upgrade flow:

1. **Read release notes** for the target K8s version
2. **Test in dev cluster first**
3. **Upgrade control plane**:
   ```bash
   aws eks update-cluster-version --name <org>-eks-dev --kubernetes-version 1.30
   ```
4. **Upgrade add-ons** (vpc-cni, coredns, kube-proxy, EBS CSI)
5. **Recycle nodes** — Karpenter respects K8s version; replace nodes with new AMI
6. **Validate**: workloads, networking, storage, monitoring

### Pod Disruption Budgets matter

During upgrade:
- Ensure PDBs allow some unavailability (otherwise nodes can't drain)
- For critical apps, use `maxUnavailable: 1` (or %)

## Networking

### VPC requirements
- 2+ subnets in different AZs (for HA)
- Subnets tagged with `kubernetes.io/cluster/<name>: shared|owned`
- Public subnets: tagged with `kubernetes.io/role/elb: 1`
- Private subnets: tagged with `kubernetes.io/role/internal-elb: 1`

### Pod IP allocation
- VPC CNI assigns ENI per pod
- Each ENI uses a VPC IP — plan IP space accordingly
- Custom networking + prefix delegation can extend pod density

### CNI prefix delegation (recommended for high-density nodes)

```bash
kubectl set env daemonset aws-node -n kube-system \
  ENABLE_PREFIX_DELEGATION=true
```

Allows assigning /28 prefixes (16 IPs) per ENI, increasing pod density.

## Common issues

### Issue: pods stuck in Pending
Check:
1. `kubectl describe pod <name>` — look at events
2. NodePool capacity reached?
3. Resource requests too high for any instance type?
4. PV available?
5. Custom scheduler / taint mismatch?

### Issue: node NotReady after Karpenter provisions
Causes:
1. CNI IP exhaustion (rare with prefix delegation)
2. Add-on not ready (vpc-cni, coredns)
3. Node trying to register but stuck (check `kubelet` logs)

### Issue: pod can't reach AWS API
Check IRSA:
```bash
kubectl describe sa <name> -n <ns>     # Has eks.amazonaws.com/role-arn?
kubectl describe pod <name> -n <ns>    # Has projected token?
```

### Issue: "unauthorized" when accessing K8s API
Check `aws-auth` ConfigMap (legacy) or `EKS Access Entries` (modern):
```bash
# Legacy: aws-auth
kubectl get cm aws-auth -n kube-system -o yaml

# Modern: Access Entries
aws eks list-access-entries --cluster-name <org>-eks-dev
```

## Cost optimization

### Right-size instance families

```bash
# Get current node usage
kubectl top nodes

# Identify under-utilized
kubectl describe node | grep -A5 "Allocated"
```

If consistently <40% utilized, consider smaller instance type (Karpenter handles this automatically with consolidation).

### Spot instances

Mix on-demand and spot in NodePool. Karpenter falls back to on-demand if spot unavailable. ScaleOps tooling at <org> further optimizes this.

### Graviton (arm64)

ARM-based instances are 20-40% cheaper. Requires multi-arch container images (see `ci-cd-conventions` steering).

## Backup and DR

- **etcd**: managed by EKS (cluster-level snapshots automatic)
- **PVC data**: use Velero or AWS Backup for EBS snapshots
- **Secrets**: stored in AWS Secrets Manager / SSM (not in cluster)

## Useful commands

```bash
# Switch context
kubectl config use-context dev
aws eks update-kubeconfig --name <org>-eks-dev --region us-east-1

# View cluster info
aws eks describe-cluster --name <org>-eks-dev | jq

# Check version compatibility
aws eks describe-addon-versions --addon-name vpc-cni \
  --kubernetes-version 1.30

# List all clusters
aws eks list-clusters --region us-east-1
```

## Roadmap for this skill

- [ ] Add <org>-specific Karpenter NodePool configurations
- [ ] Document AWS Auth / Access Entries config used at <org>
- [ ] Add ScaleOps integration patterns
- [ ] Add cluster upgrade runbook with <org> checklist

## Reference

- EKS docs: https://docs.aws.amazon.com/eks/
- Karpenter: https://karpenter.sh/
- AWS Best Practices: https://aws.github.io/aws-eks-best-practices/
- Related: `cost-explorer`, `iam-patterns`, `k8s-safety` (steering)
