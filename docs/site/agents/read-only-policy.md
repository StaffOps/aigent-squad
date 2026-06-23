# Read-Only Policy

**All agents are 100% read-only today.** This is the current operating posture, enforced at four independent layers.

!!! info "Not a permanent lock"
    Executing actions (remediation, rollback, etc.) is on the roadmap — it is not ruled out. But it is deliberately gated: read-only is the default until an explicit decision to enable execution is made, and only after the security guardrails (spec 14) and human-in-the-loop approval flow are implemented. A read-only agent's worst-case under prompt injection is data exfiltration; an *executing* agent's worst-case is a destructive action. See `ADR-001`.

## Four enforcement layers

### 1. System prompts

Every `prompt.md` includes explicit read-only instructions. Agents refuse modification requests and instead suggest the correct automation path (Terraform, ArgoCD, GitOps PR).

**Example correct response pattern:**
```
"Found 3 idle EC2 instances (CPU <5%):
- i-abc123 (t3.large): $50/month

To terminate via Terraform:
  File: terraform/ec2.tf
  Change: remove the resource block for i-abc123

Estimated savings: $150/month"
```

### 2. Adapter code

Datasource adapters only implement read operations — there is no code path to mutate infrastructure:

| Adapter | Allowed operations |
|---------|-------------------|
| `Boto3Adapter` | `describe_*`, `list_*`, `get_*` only |
| `KubernetesAdapter` | `get`, `list` verbs only |
| `HttpAdapter` | GET requests only |
| `AthenaAdapter` | SELECT queries only |
| `McpAdapter` | Tool allowlist (operator-curated, read-only tools only) |

### 3. IRSA scope (production)

The IAM role attached via IRSA has an explicit Deny policy on all write actions:

```json
{
  "Effect": "Deny",
  "Action": ["*:Create*", "*:Delete*", "*:Update*", "*:Put*", "*:Terminate*"],
  "Resource": "*"
}
```

### 4. Kubernetes RBAC (production)

The agent ServiceAccount is restricted to `get`/`list`/`watch` verbs via ClusterRole. Kyverno validates no privilege escalation at admission time.
