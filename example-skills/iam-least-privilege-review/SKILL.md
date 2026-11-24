---
name: iam-least-privilege-review
description: Read-only review of AWS IAM roles, users and policies for least privilege — wildcard actions/resources, admin-equivalent permissions, unused credentials and roles, risky trust policies — and how to explain an AccessDenied error.
keywords: ["least privilege", "overly permissive", "too many permissions", "admin access", administratoraccess, "wildcard", "action *", "resource *", "iam policy", "iam review", "iam audit", "access denied", accessdenied, "not authorized to perform", "trust policy", "assume role", "access key", "access keys", "unused role", "unused roles", "last used", "privilege escalation", passrole]
---

# IAM least-privilege review

Least privilege = each principal can do only what it actually does. The review compares
**granted** permissions with **used** permissions and flags dangerous grants.

Posture: read-only. Use `iam get-*`, `list-*`, `generate-service-last-accessed-details`,
`get-account-authorization-details`, IAM Access Analyzer findings and CloudTrail lookups.
Never attach, detach, edit or delete policies or keys — recommend changes to the owner.

## Step 1 — Admin-equivalent and wildcard grants

Flag statements with `Effect: Allow` and:

| Pattern | Risk |
|---------|------|
| `Action: "*"` with `Resource: "*"` (or `AdministratorAccess`) | Full admin |
| `iam:*`, `iam:PassRole` on `*`, `iam:CreatePolicyVersion`, `iam:AttachRolePolicy`, `iam:PutRolePolicy` | Privilege escalation paths |
| `sts:AssumeRole` on `*` | Can assume any role that trusts it |
| `<service>:*` on `*` (e.g. `s3:*`, `kms:*`, `secretsmanager:*`) | Broad data access |
| `NotAction` / `NotResource` with Allow | Grants everything except a list — easy to over-grant |
| No `Condition` on sensitive actions where one is natural (source VPC, org ID, MFA) | Missing guardrail |

Remember the **effective** permission is the union of identity policies, group policies,
inline policies, minus explicit denies, bounded by permission boundaries and SCPs.

## Step 2 — Granted vs used

- `generate-service-last-accessed-details` → services never used or not used for 90+ days
  are candidates to remove.
- IAM Access Analyzer (if enabled) can generate a policy from CloudTrail activity — a good
  starting point for a tightened policy.
- Credential report (`get-credential-report`): users with console passwords and no MFA,
  access keys older than the rotation policy, keys never used, users with no activity.
- Roles with `RoleLastUsed` empty or very old.

## Step 3 — Trust policies (who can assume the role)

| Trust pattern | Risk |
|---------------|------|
| `Principal: {"AWS": "*"}` without strong conditions | Anyone in any account can try to assume |
| Another account's root without `sts:ExternalId` (third-party access) | Confused-deputy risk |
| OIDC federation (CI, Kubernetes service accounts) without `sub` condition | Any repo/branch or any service account in the provider could assume |
| Overly broad service principals | Check the service actually needs it |

## Step 4 — Explaining AccessDenied

The error message usually names the principal, action and resource. Check in order:

1. Is the action allowed in any identity policy for that principal (correct ARN/resource format)?
2. Is there an explicit `Deny` (identity, resource policy, permission boundary)?
3. Does an SCP or resource control policy in the organization deny it?
4. Does the **resource policy** (bucket, KMS key, queue) allow the principal? KMS and
   cross-account access need both sides.
5. Do `Condition` keys fail (wrong region, missing tag, no MFA, VPC endpoint mismatch)?
6. Is the caller the principal you think it is? (`sts get-caller-identity`; for pods, the
   service-account role vs the node role.)

CloudTrail records the denied call with `errorCode: AccessDenied` and often the reason.

## Answer format

For each finding: principal, statement (action/resource/condition), why it is risky,
evidence of use or non-use, and a concrete tightened alternative for the owner (scoped
resource ARNs, specific actions, added conditions). Separate verified facts from inferred
ones; effective-permission conclusions without SCP visibility are partial — say so.
