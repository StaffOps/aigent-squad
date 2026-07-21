---
name: iam-patterns
description: "AWS IAM patterns, least privilege design, and <org> conventions. Use when designing IAM roles/policies, debugging access denied errors, or implementing IRSA (IAM Roles for Service Accounts). Covers role structures, policy patterns, IRSA, SCP, common pitfalls."
keywords: [iam, aws, irsa, scp]
---
# AWS IAM Patterns

Identity & Access Management patterns for <org> AWS environments.

## Core principles

### Least privilege
Grant the minimum permissions needed for the task. Start narrow, expand only when proven necessary.

### Explicit deny over implicit allow
A `Deny` always wins. Use to enforce hard limits.

### No long-lived keys
- Avoid IAM users with access keys
- Use IAM roles + STS AssumeRole
- For pods: IRSA (see below)
- For local dev: SSO with short-lived credentials

### Audit everything
- CloudTrail enabled in all accounts (data events for S3 too if sensitive)
- Access Analyzer flags unintended exposure
- Trusted Advisor warns of unused / risky permissions

## IAM hierarchy

```
AWS Organization
├── Organization SCPs (account-level guardrails)
└── Accounts
    ├── IAM Users (avoid — use SSO)
    ├── IAM Roles (preferred)
    │   ├── Trust policy (who can assume)
    │   └── Permission policy (what they can do)
    ├── IAM Policies (managed or inline)
    └── Identity providers (SAML, OIDC for IRSA)
```

## Role types and patterns

### 1. Service-linked roles
Pre-configured AWS-managed roles for specific services (e.g., `AWSServiceRoleForECS`). Don't modify.

### 2. EC2 instance roles
Attached to EC2 instances. Pods on EKS nodes inherit these (legacy pattern — IRSA preferred).

```json
{
  "Trust policy": {
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": {"Service": "ec2.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }]
  }
}
```

### 3. Cross-account assume role

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"AWS": "arn:aws:iam::OTHER_ACCOUNT_ID:root"},
    "Action": "sts:AssumeRole",
    "Condition": {
      "StringEquals": {
        "sts:ExternalId": "shared-secret-here"
      }
    }
  }]
}
```

`ExternalId` mitigates "confused deputy" attacks.

### 4. IRSA (IAM Roles for Service Accounts)

Modern pattern for granting AWS perms to K8s pods.

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/oidc.eks.us-east-1.amazonaws.com/id/EXAMPLED539D4633"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "oidc.eks.us-east-1.amazonaws.com/id/EXAMPLED539D4633:sub": "system:serviceaccount:my-namespace:my-app",
        "oidc.eks.us-east-1.amazonaws.com/id/EXAMPLED539D4633:aud": "sts.amazonaws.com"
      }
    }
  }]
}
```

Annotate the K8s ServiceAccount:
```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: my-app
  namespace: my-namespace
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::<ACCOUNT_ID>:role/my-app-irsa-role
```

## Policy patterns

### Read-only access (specific service)

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "s3:GetObject",
      "s3:ListBucket"
    ],
    "Resource": [
      "arn:aws:s3:::my-bucket",
      "arn:aws:s3:::my-bucket/*"
    ]
  }]
}
```

### Tag-based access (ABAC)

Allow access only to resources matching the user's `CostCenter` tag:

```json
{
  "Effect": "Allow",
  "Action": "ec2:StartInstances",
  "Resource": "*",
  "Condition": {
    "StringEquals": {
      "aws:ResourceTag/CostCenter": "${aws:PrincipalTag/CostCenter}"
    }
  }
}
```

### Time-based access

```json
{
  "Effect": "Allow",
  "Action": "*",
  "Resource": "*",
  "Condition": {
    "DateGreaterThan": {"aws:CurrentTime": "2026-01-01T00:00:00Z"},
    "DateLessThan":    {"aws:CurrentTime": "2026-12-31T23:59:59Z"}
  }
}
```

### IP-based restriction

```json
{
  "Effect": "Allow",
  "Action": "*",
  "Resource": "*",
  "Condition": {
    "IpAddress": {"aws:SourceIp": ["203.0.113.0/24"]},
    "Bool": {"aws:ViaAWSService": "false"}
  }
}
```

### MFA-required

```json
{
  "Effect": "Allow",
  "Action": "iam:DeleteUser",
  "Resource": "*",
  "Condition": {
    "Bool": {"aws:MultiFactorAuthPresent": "true"}
  }
}
```

## Service Control Policies (SCP)

Account-level guardrails set at the AWS Organization level. Cannot be overridden by account-level policies.

### Common SCPs at <org>

#### Deny resources outside allowed regions

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Deny",
    "NotAction": [
      "iam:*",
      "support:*",
      "cloudfront:*",
      "route53:*"
    ],
    "Resource": "*",
    "Condition": {
      "StringNotEquals": {
        "aws:RequestedRegion": ["us-east-1", "us-east-2"]
      }
    }
  }]
}
```

#### Require tagging on creation

```json
{
  "Effect": "Deny",
  "Action": [
    "ec2:RunInstances",
    "ec2:CreateVolume"
  ],
  "Resource": "*",
  "Condition": {
    "Null": {
      "aws:RequestTag/CostCenter": "true"
    }
  }
}
```

#### Deny public S3 buckets

```json
{
  "Effect": "Deny",
  "Action": [
    "s3:PutBucketAcl",
    "s3:PutBucketPolicy"
  ],
  "Resource": "*",
  "Condition": {
    "StringEquals": {
      "s3:x-amz-acl": ["public-read", "public-read-write"]
    }
  }
}
```

## Permission boundaries

A different concept from SCPs — limits permissions that an IAM **principal** can have.

Use case: developers can create roles, but the roles they create can never exceed a defined ceiling.

```bash
aws iam put-user-permissions-boundary \
  --user-name developer \
  --permissions-boundary arn:aws:iam::<ACCOUNT_ID>:policy/DeveloperBoundary
```

## Debugging access denied

### IAM Policy Simulator

```bash
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::<ACCOUNT_ID>:role/my-role \
  --action-names s3:GetObject \
  --resource-arns arn:aws:s3:::my-bucket/key
```

### CloudTrail

```bash
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=Username,AttributeValue=my-user \
  --start-time 2026-05-28T00:00:00Z \
  --max-results 50
```

Look for `errorCode: AccessDenied` to find what was denied and why.

### `aws sts get-caller-identity`

From inside a pod or context, verify identity:

```bash
aws sts get-caller-identity
# {
#   "UserId": "AROA...:my-app",
#   "Account": "<ACCOUNT_ID>",
#   "Arn": "arn:aws:sts::<ACCOUNT_ID>:assumed-role/my-app-irsa-role/my-app"
# }
```

## Common pitfalls

### Pitfall: `Action: "*"` in production roles

Almost always wrong. Use specific actions:
```json
"Action": ["s3:GetObject", "s3:PutObject"]
```

### Pitfall: missing `arn:aws:` prefix in resources

```json
// ❌ Wrong
"Resource": "my-bucket/*"

// ✅ Correct
"Resource": "arn:aws:s3:::my-bucket/*"
```

### Pitfall: forgot the `aud` condition in IRSA

Without `aud=sts.amazonaws.com`, the role is more vulnerable to credential theft.

### Pitfall: changing IRSA role without restarting pods

Pods cache the token until restart. After updating IAM policy or trust:
```bash
kubectl rollout restart deploy/my-app
```

### Pitfall: trust policy too permissive

```json
// ❌ Wrong — any IAM principal in account can assume
"Principal": {"AWS": "arn:aws:iam::<ACCOUNT_ID>:root"}

// ✅ Correct — only specific role/user can assume
"Principal": {"AWS": "arn:aws:iam::<ACCOUNT_ID>:role/specific-role"}
```

### Pitfall: same role attached to multiple workloads with different needs

Bad: 1 role for all microservices.
Good: per-service roles, each with minimum permissions.

### Pitfall: copy-pasting AWS managed policies

Many AWS managed policies are TOO permissive (e.g., `AmazonS3FullAccess`). Create custom policies based on actual needs.

## IAM tools

### AWS Access Analyzer
- Identifies resources with external access (cross-account, public)
- Free
- Should be enabled in all accounts

### AWS IAM Policy Simulator
- Test policies before applying
- Free, web UI + API

### Cloudsplaining (open-source)
- Detects over-privileged IAM policies
- Generates HTML reports

```bash
docker run --rm -v $(pwd):/app salesforce/cloudsplaining \
  scan --policy-file my-policy.json --output /app/report.html
```

## SSO patterns at <org>

<org> uses AWS IAM Identity Center (formerly AWS SSO). Configure local CLI:

```bash
aws configure sso
# Follow prompts:
# - SSO start URL (<org>'s portal)
# - SSO region
# - Account / Role
```

For multiple profiles:

```ini
# ~/.aws/config
[profile <org>-dev]
sso_session = bdc
sso_account_id = 111111111111
sso_role_name = DeveloperAccess

[profile <org>-prd]
sso_session = bdc
sso_account_id = <ACCOUNT_ID>
sso_role_name = ReadOnly

[sso-session bdc]
sso_start_url = https://<org>.awsapps.com/start
sso_region = us-east-1
sso_registration_scopes = sso:account:access
```

Login:
```bash
aws sso login --profile <org>-dev
aws --profile <org>-dev s3 ls
```

## Roadmap for this skill

- [ ] Add <org>-specific role naming conventions
- [ ] Document SCP set used at <org>
- [ ] Add common IRSA roles by workload type
- [ ] Add audit query patterns for unused IAM resources

## Reference

- AWS IAM docs: https://docs.aws.amazon.com/IAM/
- IRSA setup: https://docs.aws.amazon.com/eks/latest/userguide/iam-roles-for-service-accounts.html
- SCP examples: https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_scps_examples.html
- Cloudsplaining: https://github.com/salesforce/cloudsplaining
- Related: `eks-management`, `cost-explorer`, `k8s-safety` (steering)
