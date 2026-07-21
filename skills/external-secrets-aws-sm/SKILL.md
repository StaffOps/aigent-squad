---
name: external-secrets-aws-sm
description: "External Secrets Operator with AWS Secrets Manager at <org>. Use when configuring secrets for applications, debugging sync failures, or designing secret rotation patterns. Covers ExternalSecret CRD, SecretStore, refresh intervals, and common patterns."
keywords: [external-secrets-aws-sm, external, secrets, aws, sm, "external secrets", "secrets aws", "aws sm", crd]
---
# External Secrets Operator + AWS Secrets Manager

## Overview

<org> uses External Secrets Operator (ESO) to synchronize secrets from AWS Secrets Manager into Kubernetes Secrets. This eliminates hardcoded credentials and enables centralized secret management with automatic rotation.

```
AWS Secrets Manager → External Secrets Operator → K8s Secret → Pod env/volume
```

## Components

| Component | Namespace | Purpose |
|-----------|-----------|---------|
| ESO controller | `external-secrets` | Watches ExternalSecret CRDs, syncs secrets |
| ClusterSecretStore | cluster-scoped | Defines how to authenticate to AWS |
| ExternalSecret | per-namespace | Maps AWS secret fields to K8s secret keys |
| K8s Secret | per-namespace | Created/managed by ESO (target) |

## CRDs

| CRD | Scope | Purpose |
|-----|-------|---------|
| `ClusterSecretStore` | Cluster | Shared AWS connection config (one per cluster) |
| `SecretStore` | Namespace | Namespace-specific AWS connection (rarely used) |
| `ExternalSecret` | Namespace | Declares which AWS secrets to sync and how |

## ClusterSecretStore (IRSA-based)

Authentication to AWS uses IRSA — the ESO ServiceAccount assumes an IAM role with permissions to read Secrets Manager.

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ClusterSecretStore
metadata:
  name: aws-secrets-manager
spec:
  provider:
    aws:
      service: SecretsManager
      region: us-east-1
      auth:
        jwt:
          serviceAccountRef:
            name: external-secrets-sa
            namespace: external-secrets
```

The ServiceAccount has the IRSA annotation:
```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: external-secrets-sa
  namespace: external-secrets
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::<ACCOUNT_ID>:role/external-secrets-role
```

## ExternalSecret — basic example

Maps a single AWS secret (JSON) to a K8s Secret with multiple keys:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: dpm-people-api-secrets
  namespace: dpm
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: aws-secrets-manager
    kind: ClusterSecretStore
  target:
    name: dpm-people-api-secrets
    creationPolicy: Owner
  data:
    - secretKey: db-connection
      remoteRef:
        key: dpm/people-api/prd
        property: DATABASE_URL

    - secretKey: redis-url
      remoteRef:
        key: dpm/people-api/prd
        property: REDIS_CONNECTION

    - secretKey: api-key
      remoteRef:
        key: dpm/people-api/prd
        property: EXTERNAL_API_KEY
```

AWS secret `dpm/people-api/prd` contains:
```json
{
  "DATABASE_URL": "postgresql://user:pass@host:5432/db",
  "REDIS_CONNECTION": "redis://host:6379/0",
  "EXTERNAL_API_KEY": "sk-abc123..."
}
```

## refreshInterval

| Interval | Use case |
|----------|----------|
| `10m` | Default — good for most secrets |
| `1h` | Stable secrets that rarely change |
| `5m` | Secrets with automatic rotation enabled |
| `1m` | Emergency — use temporarily during rotation events |

**Warning**: very short intervals increase AWS API calls and cost.

## Multiple fields from same AWS secret

Use `dataFrom` to extract all fields at once:

```yaml
spec:
  dataFrom:
    - extract:
        key: dpm/people-api/prd
```

This creates a K8s Secret with keys matching the JSON field names.

## Template — transform secret data

```yaml
spec:
  target:
    name: dpm-people-api-secrets
    template:
      type: Opaque
      data:
        connection-string: >-
          Host={{ .db_host }};Port={{ .db_port }};
          Database={{ .db_name }};Username={{ .db_user }};
          Password={{ .db_pass }}
  data:
    - secretKey: db_host
      remoteRef:
        key: dpm/people-api/prd
        property: DB_HOST
    - secretKey: db_port
      remoteRef:
        key: dpm/people-api/prd
        property: DB_PORT
```

## Usage in <org> app Helm chart

The <org> `app` chart has built-in ExternalSecret support:

```yaml
# values.yaml for a service
externalSecret:
  enabled: true
  refreshInterval: 1h
  secretStoreRef:
    name: aws-secrets-manager
    kind: ClusterSecretStore
  data:
    - secretKey: DATABASE_URL
      remoteRef:
        key: dpm/people-api/prd
        property: DATABASE_URL
    - secretKey: REDIS_URL
      remoteRef:
        key: dpm/people-api/prd
        property: REDIS_CONNECTION
```

The chart creates the `ExternalSecret` and references the resulting K8s Secret in the pod's env via `secretKeyRef`.

## Debugging sync failures

### Step 1: Check ExternalSecret status

```bash
kubectl get externalsecret -n <namespace>
# STATUS column: SecretSynced or SecretSyncedError

kubectl describe externalsecret <name> -n <namespace>
```

### Step 2: Common errors

| Error | Cause | Fix |
|-------|-------|-----|
| `AccessDeniedException` | IRSA role lacks permissions | Add `secretsmanager:GetSecretValue` to IAM policy |
| `ResourceNotFoundException` | AWS secret name wrong | Verify `remoteRef.key` matches exactly |
| `InvalidRequestException` | Property not found in JSON | Check `remoteRef.property` field name |
| `SecretStore not ready` | ClusterSecretStore misconfigured | Check IRSA annotation and role trust policy |

### Step 3: Verify SecretStore connectivity

```bash
kubectl get clustersecretstore aws-secrets-manager -o yaml
# Check status.conditions[].status == "True"
```

### Step 4: Verify K8s Secret was created

```bash
kubectl get secret <target-name> -n <namespace>
kubectl get secret <target-name> -n <namespace> -o jsonpath='{.data}' | jq 'keys'
```

## Secret rotation pattern

1. Enable rotation in AWS Secrets Manager (Lambda-based)
2. Set `refreshInterval: 5m` on the ExternalSecret
3. ESO detects the new secret value on next refresh
4. K8s Secret is updated
5. Pods pick up new value on next restart (or use volume mount for live reload)

**Note**: env vars from `secretKeyRef` require pod restart. Volume-mounted secrets update live.

## Naming conventions

AWS secret names follow the pattern:
```
<domain-sigla>/<service-name>/<environment>
```

Examples: `dpm/people-api/prd`, `dcp/receita-process/dev`, `devops/harbor/prd`

## Anti-patterns

- ❌ Secrets in ConfigMaps (not encrypted at rest, visible in plain text)
- ❌ Secrets in Helm `values.yaml` files (committed to Git)
- ❌ Long `refreshInterval` (>1h) for secrets with rotation enabled
- ❌ Missing IRSA permissions (silent failure — ESO logs error but pod sees stale secret)
- ❌ Using `SecretStore` per namespace when `ClusterSecretStore` suffices
- ❌ Hardcoding secret values in pod env definitions (use `secretKeyRef`)
- ❌ Sharing one AWS secret across unrelated services (blast radius)
- ❌ Not checking `ExternalSecret` status after deployment
- ❌ Using `creationPolicy: Merge` without understanding existing secret ownership

## Related

- `cloud-security-<org>` steering — secrets management flow
- `helm-chart-app` skill — externalSecret section in chart values
- `iam-patterns` skill — IRSA role design for ESO
