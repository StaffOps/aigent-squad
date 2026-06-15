# Read-Only Policy

**All agents are 100% read-only. No exceptions.**

## Enforcement (4 layers)

### 1. System prompts
Every `prompt.md` includes explicit read-only instructions. Agents refuse modification requests.

### 2. DatasourceAdapter code
Adapters are hardcoded to read-only operations:

| Adapter | Allowed operations |
|---------|-------------------|
| `Boto3Adapter` | `describe_*`, `list_*`, `get_*` only |
| `KubernetesAdapter` | `get`, `list` verbs only |
| `HttpAdapter` | GET requests only (configurable) |
| `AthenaAdapter` | SELECT queries only |

Write operations are not implemented — there is no code path to mutate infrastructure.

### 3. IRSA scope (production)
IAM role attached via IRSA has explicit Deny on all write actions:
```json
{"Effect": "Deny", "Action": ["*:Create*", "*:Delete*", "*:Update*", "*:Put*"], "Resource": "*"}
```

### 4. Kyverno policies (production)
K8s RBAC restricts the ServiceAccount to `get`/`list`/`watch` verbs. Kyverno validates no privilege escalation.

## Agent behavior

Agents analyze and recommend. They never execute changes. Correct response pattern:

```
"Found 3 idle EC2 instances (CPU <5%):
- i-abc123: $50/month

To terminate via Terraform:
  File: terraform/ec2.tf
  Change: remove resource block

Estimated savings: $150/month"
```

Agents point to automation (Terraform, ArgoCD, GitOps PRs) — never suggest direct CLI mutations.
