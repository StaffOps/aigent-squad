# Design: CI/CD Pipeline (GitHub Actions)

## Architecture

4-stage GitHub Actions pipeline, reusing the dockerized harness (spec 23) in the test stage — dev↔CI parity.

```
push/PR ─▶ test (harness spec 23, cov≥90 + ruff) ─▶ build-dev (multi-arch + Trivy + SBOM, tag <sha>)
                                                          └─▶ [optional demo] ─▶ release (manual, v<semver>)
```

Mirrors the `staffops-chaitops` convention (which already has `.github/workflows/` test/build/lint/docs).

## Stages

| Stage | Does | Trigger |
|-------|------|---------|
| `test` | `pytest --cov-fail-under=90` via harness (spec 23) + `ruff` + `mkdocs build --strict` (spec 24) | every push/PR |
| `build-dev` | `docker buildx` multi-arch (amd64+arm64) per service; Trivy; SBOM CycloneDX; push `<sha>` | push to default |
| `demo` (optional) | brings up stack + smoke (1 single query + 1 cross-domain) | push/manual |
| `release` | tag `v<semver>`; push stable | **manual** |

## Decisions and trade-offs

### Decision 1: GitHub Actions (not GitLab CI)
**Choice**: pipeline in GitHub Actions.
**Justification**: the repo **is** on GitHub (`github.com:karlipegomes/AIgent-squad`); the docs citing GitLab CI are fiction (gitops F8). The ecosystem (chaitops) already uses GitHub Actions — consistency.
**Trade-off**: if ever migrated to GitLab, the 4-stage design translates 1:1.

### Decision 2: CI reuses the harness from spec 23 (not its own test path)
**Choice**: the `test` stage calls the **same** Dockerfile/command from spec 23.
**Justification**: kills "passes local, fails in CI" (environment divergence). A single harness, a single 90% gate.
**Trade-off**: couples CI to the existence of spec 23 — desirable (it's a declared dependency).

### Decision 3: OIDC for AWS (no long-lived key)
**Choice**: OIDC federation GitHub→AWS for ECR push.
**Justification**: `cloud-security` steering forbids long-lived keys; OIDC gives ephemeral tokens.
**Trade-off**: initial OIDC provider setup — one-time only.

## Invariants
- Coverage <90% **blocks the merge** (gate, not advisory).
- Multi-arch images (amd64+arm64) — single-arch breaks Graviton.
- `latest` forbidden in prod; immutable tags `<sha>`/`v<semver>`.
- No long-lived AWS credentials (OIDC).
- `test` uses the harness from spec 23 (no second path).

## External dependencies
| Service | Usage |
|---------|-------|
| GitHub Actions | runner |
| ECR/Harbor | registry |
| Trivy, Syft/CycloneDX | scan + SBOM |
| AWS OIDC | ephemeral credentials |

## Verification
- Test PR: confirm that coverage forced <90% **fails** the check.
- Confirm multi-arch manifest (amd64+arm64) in the published image.
- `mkdocs build --strict` green in CI.

## Risks
- Multi-arch build slow (QEMU arm64) → layer caching + buildx; acceptable.
- Misconfigured OIDC → test ECR push in dev before prod.
