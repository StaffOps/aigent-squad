---
name: pipeline-template-apps
description: "GitLab CI pipeline patterns for <ORG> application domains. Use when creating or modifying CI/CD pipelines, understanding stage flow, or troubleshooting pipeline failures. Covers shared templates, domain-specific repos, branch-to-environment mapping, and common patterns."
keywords: [pipeline-apps, pipeline, apps, "pipeline apps", gitlab, ci, cd]
---
# <ORG> GitLab CI Pipeline Patterns

## Overview

<ORG> uses modular GitLab CI pipelines with domain-specific repos and shared templates. Each business domain has its own CI/CD repo containing pipeline definitions for all services in that domain.

## Domain-Specific Pipeline Repos

9 repos in `/home/karlipegomes/Documents/<ORG>/00-GITLAB/PIPELINES/`:

| Repo | Domain | CostCenter |
|------|--------|------------|
| `dpm-cicd` | Data Platform | Program-DataPlatform |
| `dcp-cicd` | Data Capture | Platform-DataCapture / Program-DataCapture |
| `mdt-cicd` | Metadata | Platform-Metadata |
| `bm-cicd` | Billing & Monetization | Platform-BaseServiceLayer-Billing |
| `acum-cicd` | Access Control & User Mgmt | Platform-BaseServiceLayer-AccessControl |
| `apps-cicd` | Apps | Program-Apps |
| `ai-cicd` | AI Services | Platform-AIServices |
| `ctp-cicd` | Client Tools & Programs | Program-ClientToolsAndPrograms |
| `deng-cicd` | Data Engineering / Quality | Platform-DataEngineering |

## Shared Templates

Located in `00-GITLAB/PIPELINES/gitlab-ci/`:

| Template | Purpose |
|----------|---------|
| `terraform_validate_plan.yaml` | Terraform init + validate + plan (runs on MRs) |
| `terraform_apply.yaml` | Terraform apply (runs on merge to protected branch) |
| `cloud_front_template.yaml` | CloudFront invalidation after frontend deploy |
| `cypress.yaml` | E2E testing with Cypress |
| `oidc.yaml` | AWS OIDC federation for short-lived credentials |
| `portal_ecs.yaml` | ECS-specific deployment (legacy, being replaced) |

Usage in domain pipelines:

```yaml
include:
  - project: 'devops/gitlab-ci'
    ref: main
    file:
      - '/templates/oidc.yaml'
      - '/templates/terraform_validate_plan.yaml'
```

## Standard Stages

```
release_notes -> pre-build -> build -> test -> review -> deploy -> rollback
```

| Stage | Purpose | Trigger |
|-------|---------|---------|
| `release_notes` | Generate CHANGELOG from conventional commits | Merge to `production` |
| `pre-build` | Lint, security scan (gitleaks, trivy), validate | All branches |
| `build` | Docker multi-stage build + push to Harbor | All branches |
| `test` | Unit + integration tests | All branches |
| `review` | Deploy ephemeral review environment | `feature/*` (optional) |
| `deploy` | Deploy to target environment | Protected branches only |
| `rollback` | Manual rollback to previous image | Protected branches (manual) |

## Branch to Environment Mapping

| Branch | Environment | Cluster | Auto-deploy |
|--------|-------------|---------|-------------|
| `feature/*` | — (CI only) | — | No |
| `development` | DEV | `<org>-workloads-dev-nv` | Yes |
| `homologation` | HML | `<org>-workloads-prd-nv` (hml namespace) | Yes |
| `production` | PRD | `<org>-workloads-prd-nv` | Yes |
| `production` + `BTC_ON_EKS` | BTC | `<org>-workloads-prd-nv` (batch namespace) | Yes |

## Feature Flags (ECS to EKS Migration)

Dual-path deployment controlled by CI variables:

```yaml
variables:
  DEV_ON_EKS: "true"    # Deploy DEV to EKS (instead of ECS)
  PRD_ON_EKS: "true"    # Deploy PRD to EKS
  BTC_ON_EKS: "false"   # Deploy BTC to EKS (batch workloads)
```

When flag is `true`: pipeline triggers ArgoCD sync (EKS path).
When flag is `false`: pipeline updates ECS task definition (legacy path).

## Build Patterns

### Docker Multi-Stage Build

```yaml
build:image:
  stage: build
  script:
    - docker buildx build
        --platform linux/amd64
        -t ${IMAGE}:${CI_COMMIT_SHORT_SHA}
        --build-arg VERSION=${VERSION}
        --push .
```

### Dual-Architecture Build

```yaml
build:amd64:
  stage: build
  tags: [docker-amd64]
  script:
    - docker buildx build --platform linux/amd64
        -t ${IMAGE}:${CI_COMMIT_SHORT_SHA}
        --push .

build:arm64:
  stage: build
  tags: [docker-arm64]
  script:
    - docker buildx build --platform linux/arm64
        -t ${IMAGE}:${CI_COMMIT_SHORT_SHA}-arm64-graviton
        --push .
```

### Cosign Signing

```yaml
build:sign:
  stage: build
  needs: [build:amd64]
  script:
    - echo "${COSIGN_PASSWORD}" | cosign sign
        --key ${COSIGN_KEY_PATH}
        --new-bundle-format=false
        ${IMAGE}@${DIGEST}
        -y
  rules:
    - if: $CI_COMMIT_BRANCH =~ /^(production|homologation)$/
```

**Critical**: `--new-bundle-format=false` required for Harbor visibility.

## Image Tagging

| Tag | When | Example |
|-----|------|---------|
| `<short_sha>` | Every build (amd64) | `a1b2c3d` |
| `<short_sha>-arm64-graviton` | Every build (arm64) | `a1b2c3d-arm64-graviton` |
| `latest` | Merge to main/production only | `latest` |

Registry: `harbor.<org>.com/<HARBOR_PROJECT>/<service-name>`

## Deploy Patterns

### EKS (ArgoCD Sync)

```yaml
deploy:eks:
  stage: deploy
  script:
    - git clone ${GITOPS_REPO}
    - cd ${ENV_DIR}
    - yq -i '.image.tag = "'${CI_COMMIT_SHORT_SHA}'"' values.yaml
    - git commit -am "deploy: ${CI_PROJECT_NAME} ${CI_COMMIT_SHORT_SHA}"
    - git push
  rules:
    - if: $CI_COMMIT_BRANCH == "production" && $PRD_ON_EKS == "true"
```

ArgoCD auto-syncs from the GitOps repo after the image tag update.

### ECS (Task Definition Update)

```yaml
deploy:ecs:
  stage: deploy
  script:
    - aws ecs update-service
        --cluster ${ECS_CLUSTER}
        --service ${SERVICE_NAME}
        --task-definition ${TASK_DEF}:${NEW_REVISION}
        --force-new-deployment
  rules:
    - if: $CI_COMMIT_BRANCH == "production" && $PRD_ON_EKS == "false"
```

## Rollback

Manual stage that reverts to the previous image tag:

```yaml
rollback:
  stage: rollback
  when: manual
  script:
    - PREVIOUS_TAG=$(git log --skip=1 -1 --format=%h)
    - yq -i '.image.tag = "'${PREVIOUS_TAG}'"' ${GITOPS_REPO}/values.yaml
    - git commit -am "rollback: ${CI_PROJECT_NAME} to ${PREVIOUS_TAG}"
    - git push
  rules:
    - if: $CI_COMMIT_BRANCH =~ /^(production|homologation)$/
```

## Shared Variables

```yaml
variables:
  CI_COMMIT_SHORT_SHA: ${CI_COMMIT_SHORT_SHA}  # Auto-set by GitLab
  IMAGE: "harbor.<org>.com/${HARBOR_PROJECT}/${CI_PROJECT_NAME}"
  HARBOR_PROJECT: "<org>-images"
  COSIGN_KEY_PATH: "/run/secrets/cosign.key"
  AWS_REGION: "us-east-1"
```

## OIDC (AWS Credentials)

Short-lived AWS credentials via GitLab OIDC federation (no long-lived access keys):

```yaml
.oidc_aws:
  id_tokens:
    AWS_TOKEN:
      aud: https://gitlab.<org>.internal
  before_script:
    - >
      export $(printf "AWS_ACCESS_KEY_ID=%s AWS_SECRET_ACCESS_KEY=%s AWS_SESSION_TOKEN=%s"
      $(aws sts assume-role-with-web-identity
        --role-arn ${AWS_ROLE_ARN}
        --role-session-name "gitlab-ci-${CI_PIPELINE_ID}"
        --web-identity-token ${AWS_TOKEN}
        --duration-seconds 3600
        --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]'
        --output text))
```

## Terraform Stages

### Validate + Plan (on MR)

```yaml
terraform:plan:
  extends: .terraform_validate_plan
  variables:
    TF_DIR: "terraform/"
    TF_WORKSPACE: ${ENVIRONMENT}
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
```

### Apply (on merge to protected branch)

```yaml
terraform:apply:
  extends: .terraform_apply
  variables:
    TF_DIR: "terraform/"
    TF_WORKSPACE: ${ENVIRONMENT}
  rules:
    - if: $CI_COMMIT_BRANCH =~ /^(development|homologation|production)$/
```

Plan output is posted as MR comment for review before merge.

## Pipeline Flow Diagram

```
feature/* branch:
  pre-build -> build -> test -> (review optional)

development branch:
  pre-build -> build -> test -> deploy:DEV

homologation branch:
  pre-build -> build -> test -> deploy:HML -> rollback (manual)

production branch:
  release_notes -> pre-build -> build -> test -> deploy:PRD -> rollback (manual)
```

## Anti-patterns

- Hardcoded image tags in deploy scripts (use `CI_COMMIT_SHORT_SHA`)
- Skipping test stage ("it's just a config change")
- Deploying from `feature/*` branches to shared environments
- Manual `docker push` to Harbor (must go through pipeline for audit trail)
- Long-lived AWS credentials (use OIDC federation)
- `terraform apply` without prior `plan` review
- Missing cosign signing for HML/PRD images
- Single-arch builds (breaks Graviton node scheduling)
- Skipping `pre-build` security scans (gitleaks, trivy)
- Using `latest` tag in deploy manifests (non-reproducible)
- Direct ECS/EKS API calls without feature flag check
