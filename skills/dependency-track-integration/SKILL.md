---
name: dependency-track-integration
description: "Use when integrating DependencyTrack with CI/CD pipelines, configuring project hierarchies, uploading SBOMs via API, or managing vulnerability policies and notifications. Covers REST API patterns, project structure, BOM upload, policy configuration, webhooks, and <org>-specific deployment."
keywords: [dependency-track, dependency, track, "dependency track", ci, cd, rest, bom]
---
# DependencyTrack Integration

DependencyTrack is <org>'s component intelligence platform — tracks every dependency across all services, matches against vulnerability databases, and enforces license/security policies.

## Deployment at <org>

| Attribute | Value |
|-----------|-------|
| Namespace | `dependency-track` (core-devops cluster) |
| API Server | `dependency-track-apiserver.dependency-track:8080` |
| Frontend | Ingress via NGINX (internal) |
| Auth | API key per team (header: `X-Api-Key`) |
| Data sources | NVD, OSV, GitHub Advisories, Sonatype OSS Index |

## API authentication

All API calls require the `X-Api-Key` header:

```bash
curl -s "${DTRACK_URL}/api/v1/project" \
  -H "X-Api-Key: ${DTRACK_API_KEY}"
```

Teams get individual API keys with scoped permissions:
- `BOM_UPLOAD` — upload SBOMs
- `VIEW_PORTFOLIO` — read projects/components
- `VULNERABILITY_ANALYSIS` — view/audit vulnerabilities
- `POLICY_MANAGEMENT` — manage policies (admin only)

## Project hierarchy

### Structure

```
Parent: "<cost-center>" (domain)
├── Child: "dpm-people-api" (service)
│   ├── Version: "a1b2c3d" (commit SHA)
│   └── Version: "v1.2.3" (release)
├── Child: "dpm-people-worker"
└── Child: "dpm-shared-lib"
```

### Create parent project

```bash
curl -X PUT "${DTRACK_URL}/api/v1/project" \
  -H "X-Api-Key: ${DTRACK_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "<cost-center>",
    "version": "latest",
    "description": "DPM domain — all microservices",
    "tags": [{"name": "domain:dpm"}, {"name": "env:prd"}]
  }'
```

### Create child project

```bash
curl -X PUT "${DTRACK_URL}/api/v1/project" \
  -H "X-Api-Key: ${DTRACK_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "dpm-people-api",
    "version": "v1.2.3",
    "parent": {"uuid": "<parent-uuid>"},
    "tags": [{"name": "lang:dotnet"}, {"name": "env:prd"}]
  }'
```

## BOM upload

### Standard upload (base64-encoded)

```bash
SBOM_BASE64=$(base64 -w0 sbom.cdx.json)

curl -X PUT "${DTRACK_URL}/api/v1/bom" \
  -H "X-Api-Key: ${DTRACK_API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{
    \"projectName\": \"dpm-people-api\",
    \"projectVersion\": \"${CI_COMMIT_SHORT_SHA}\",
    \"parentName\": \"<cost-center>\",
    \"parentVersion\": \"latest\",
    \"autoCreate\": true,
    \"bom\": \"${SBOM_BASE64}\"
  }"
```

### Multipart upload (large SBOMs)

```bash
curl -X POST "${DTRACK_URL}/api/v1/bom" \
  -H "X-Api-Key: ${DTRACK_API_KEY}" \
  -F "project=${PROJECT_UUID}" \
  -F "bom=@sbom.cdx.json"
```

### Auto-create behavior

When `autoCreate: true`:
- Project created if it doesn't exist
- Parent project created if `parentName` specified and doesn't exist
- Tags inherited from parent (if configured)
- New version creates a new project version entry

## GitLab CI integration

### Complete stage

```yaml
variables:
  DTRACK_URL: "http://dependency-track-apiserver.dependency-track:8080"

sbom:upload:
  stage: security
  image: curlimages/curl:latest
  needs: [build:image]
  script:
    # Generate SBOM from built image
    - apk add --no-cache trivy
    - trivy image --format cyclonedx --output sbom.cdx.json ${IMAGE}@${DIGEST}

    # Upload to DependencyTrack
    - SBOM_BASE64=$(base64 -w0 sbom.cdx.json)
    - |
      HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        -X PUT "${DTRACK_URL}/api/v1/bom" \
        -H "X-Api-Key: ${DTRACK_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "{
          \"projectName\": \"${CI_PROJECT_NAME}\",
          \"projectVersion\": \"${CI_COMMIT_SHORT_SHA}\",
          \"parentName\": \"${COST_CENTER}\",
          \"parentVersion\": \"latest\",
          \"autoCreate\": true,
          \"bom\": \"${SBOM_BASE64}\"
        }")
    - |
      if [ "$HTTP_CODE" != "200" ]; then
        echo "ERROR: SBOM upload failed (HTTP $HTTP_CODE)"
        exit 1
      fi
  rules:
    - if: $CI_COMMIT_BRANCH == "main"
    - if: $CI_COMMIT_BRANCH == "development"
```

### Policy evaluation gate

```yaml
sbom:policy-check:
  stage: security
  needs: [sbom:upload]
  script:
    # Wait for analysis to complete (async)
    - sleep 30

    # Check policy violations
    - |
      VIOLATIONS=$(curl -s "${DTRACK_URL}/api/v1/violation/project/${PROJECT_UUID}" \
        -H "X-Api-Key: ${DTRACK_API_KEY}" | jq 'length')
    - |
      if [ "$VIOLATIONS" -gt "0" ]; then
        echo "POLICY VIOLATIONS DETECTED: $VIOLATIONS"
        curl -s "${DTRACK_URL}/api/v1/violation/project/${PROJECT_UUID}" \
          -H "X-Api-Key: ${DTRACK_API_KEY}" | jq '.[] | {type, policyCondition: .policyCondition.subject}'
        exit 1
      fi
  allow_failure: true  # Warn mode initially
```

## Vulnerability policies

### Create policy (API)

```bash
# Create severity-based policy
curl -X PUT "${DTRACK_URL}/api/v1/policy" \
  -H "X-Api-Key: ${DTRACK_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Block Critical Vulnerabilities",
    "operator": "ANY",
    "violationState": "FAIL",
    "policyConditions": [{
      "subject": "SEVERITY",
      "operator": "IS",
      "value": "CRITICAL"
    }]
  }'
```

### Policy types

| Subject | Operator | Example | Use case |
|---------|----------|---------|----------|
| `SEVERITY` | `IS` | `CRITICAL` | Block critical CVEs |
| `LICENSE` | `IS` | `GPL-3.0` | Block copyleft in proprietary |
| `COMPONENT_AGE` | `NUMERIC_GREATER_THAN` | `365` | Flag stale dependencies |
| `VULNERABILITY_ID` | `IS` | `CVE-2024-1234` | Blacklist specific CVE |
| `COORDINATES` | `MATCHES` | `pkg:npm/lodash@<4.17.21` | Block known-bad versions |

### Exemptions (analysis decisions)

```bash
# Mark vulnerability as "not affected" for a specific component
curl -X POST "${DTRACK_URL}/api/v1/analysis" \
  -H "X-Api-Key: ${DTRACK_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "project": "<project-uuid>",
    "component": "<component-uuid>",
    "vulnerability": "<vuln-uuid>",
    "analysisState": "NOT_AFFECTED",
    "analysisJustification": "CODE_NOT_REACHABLE",
    "comment": "Affected function not called in our codebase. Verified via static analysis.",
    "isSuppressed": true
  }'
```

### Justification values

| Value | Meaning |
|-------|---------|
| `CODE_NOT_PRESENT` | Vulnerable code not included in build |
| `CODE_NOT_REACHABLE` | Code present but never executed |
| `REQUIRES_CONFIGURATION` | Only exploitable with specific config we don't use |
| `REQUIRES_DEPENDENCY` | Needs another vulnerable dep we don't have |
| `REQUIRES_ENVIRONMENT` | Only exploitable in environments we don't run |
| `PROTECTED_BY_MITIGATING_CONTROL` | WAF, network policy, etc. blocks exploitation |

## Webhooks & notifications

### Configure webhook (Slack)

```bash
curl -X PUT "${DTRACK_URL}/api/v1/notification/publisher" \
  -H "X-Api-Key: ${DTRACK_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Slack Security",
    "publisherClass": "org.dependencytrack.notification.publisher.SlackPublisher",
    "templateMimeType": "application/json",
    "defaultPublisher": false
  }'
```

### Notification rules

| Event | Destination | Condition |
|-------|-------------|-----------|
| `NEW_VULNERABILITY` | Slack `#security-alerts` | Severity >= HIGH |
| `POLICY_VIOLATION` | Slack + Jira | Any violation |
| `BOM_PROCESSED` | — (silent) | — |
| `PROJECT_AUDIT_CHANGE` | Slack `#security-audit` | Any |

### Alert rule creation

```bash
curl -X PUT "${DTRACK_URL}/api/v1/notification/rule" \
  -H "X-Api-Key: ${DTRACK_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Critical Vulns to Slack",
    "enabled": true,
    "notifyOn": ["NEW_VULNERABILITY"],
    "publisher": {"uuid": "<publisher-uuid>"},
    "notificationLevel": "ERROR"
  }'
```

## Useful API queries

```bash
# List all projects
curl -s "${DTRACK_URL}/api/v1/project?limit=100" \
  -H "X-Api-Key: ${DTRACK_API_KEY}" | jq '.[].name'

# Get project vulnerabilities
curl -s "${DTRACK_URL}/api/v1/vulnerability/project/${UUID}" \
  -H "X-Api-Key: ${DTRACK_API_KEY}" | jq '.[] | {vulnId, severity, component: .component.name}'

# Get project policy violations
curl -s "${DTRACK_URL}/api/v1/violation/project/${UUID}" \
  -H "X-Api-Key: ${DTRACK_API_KEY}"

# Search component across all projects
curl -s "${DTRACK_URL}/api/v1/component/identity?cpe=cpe:/a:apache:log4j" \
  -H "X-Api-Key: ${DTRACK_API_KEY}"

# Get metrics (portfolio-wide)
curl -s "${DTRACK_URL}/api/v1/metrics/portfolio/current" \
  -H "X-Api-Key: ${DTRACK_API_KEY}"
```

## Anti-patterns

- ❌ Single flat project for all services (no hierarchy — impossible to track ownership)
- ❌ Exemptions/suppressions without justification comment (audit failure)
- ❌ Shared API key across all teams (no per-team audit trail)
- ❌ Uploading SBOM only at release (miss vulnerabilities discovered between releases)
- ❌ Ignoring `NOT_AFFECTED` analysis — just suppressing without proper justification
- ❌ No webhook notifications (findings sit unnoticed until next audit)
- ❌ Policy violations in `WARN` mode forever (never graduating to `FAIL`)
- ❌ Manual BOM upload via UI (must be CI-automated)
- ❌ Not setting `parentName` (orphan projects with no domain context)
- ❌ Using project version `latest` for every upload (overwrites history)
- ❌ Skipping license policy (GPL in proprietary code = legal risk)

## Related

- `sbom-vulnerability-management` skill — full pipeline overview
- `container-image-apko` skill — apko auto-generates SBOM
- `ci-cd-conventions` steering — pipeline stage structure
- `cloud-security-<org>` steering — vulnerability SLA requirements
