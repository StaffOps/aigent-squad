# Verifying Releases

Every versioned release of `ghcr.io/staffops/aigent-squad` is signed and attested
using [Sigstore](https://sigstore.dev/) (keyless OIDC) and
[GitHub Attestations](https://docs.github.com/en/actions/security-guides/using-artifact-attestations-to-establish-provenance-for-builds).

## Prerequisites

- [cosign](https://docs.sigstore.dev/cosign/system_config/installation/) ≥ 2.0
- [gh CLI](https://cli.github.com/) (for attestation verification)

## Verify a release

```bash
# Replace the digest with the one from your pull:
#   docker pull ghcr.io/staffops/aigent-squad:0.5.0
#   docker inspect --format='{{.RepoDigests}}' ghcr.io/staffops/aigent-squad:0.5.0
DIGEST="sha256:<your-digest-here>"

# 1. Verify cosign signature (proves the image was built by this repo's CI)
cosign verify \
  --certificate-identity-regexp "^https://github.com/StaffOps/aigent-squad/" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  "ghcr.io/staffops/aigent-squad@${DIGEST}"

# 2. Verify build provenance attestation
gh attestation verify \
  "oci://ghcr.io/staffops/aigent-squad@${DIGEST}" \
  --owner StaffOps
```

Both commands must succeed. If either fails, **do not use the image**.

## Or use the Makefile shortcut

```bash
make verify-release DIGEST=sha256:<your-digest>
```

## What the signature proves

| Claim | Evidence |
|-------|----------|
| Built by `StaffOps/aigent-squad` CI | `certificate-identity-regexp` matches the workflow file path |
| GitHub Actions OIDC issued the token | `certificate-oidc-issuer` = `https://token.actions.githubusercontent.com` |
| Image was not tampered after push | cosign signature over the manifest-list digest |
| Build inputs are traceable | Provenance attestation links to the exact commit + workflow |

## ⚠️ Common mistakes

### Loose identity regexp

```bash
# ❌ WRONG — accepts a signature from ANY GitHub Actions workflow in ANY repo
cosign verify --certificate-identity-regexp ".*" ...

# ✅ CORRECT — only accepts this specific repo
cosign verify --certificate-identity-regexp "^https://github.com/StaffOps/aigent-squad/" ...
```

A loose `--certificate-identity-regexp` (e.g. `.*`) silently accepts a signature from
**any** workflow in **any** repo. This is the most common keyless misconfiguration.

### Verifying by tag then following `latest`

```bash
# You verified v0.5.0 — great
cosign verify ... ghcr.io/staffops/aigent-squad@sha256:abc123

# Later you pull :latest — BAD: tag overwrite is precisely the threat
# the signature exists to detect. Always pin by digest after verification.
docker pull ghcr.io/staffops/aigent-squad:latest  # ← no guarantee
```

### Negative example (expected failure)

```bash
# This MUST fail — proves the verifier is not silently passing everything
$ cosign verify \
    --certificate-identity-regexp "^https://github.com/StaffOps/aigent-squad/" \
    --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
    ghcr.io/library/busybox:latest

Error: no matching signatures: none of the expected identities matched...
```

If this does NOT fail, your cosign installation or arguments are misconfigured.

## Why `latest` is deliberately unsigned

Only versioned releases (tags `v*`) are signed. The `latest` tag is a convenience
for development and is **not** a trust boundary. Signing `latest` would teach
consumers to verify a mutable tag — which is the habit this system exists to break.

## SBOM

The SBOM (CycloneDX format) is:
1. Attached as a GitHub Release asset (`sbom.cdx.json`)
2. Stored as an OCI attestation in the registry (discoverable via `cosign tree`)

```bash
# Download from GitHub Release
gh release download v0.5.0 --pattern "sbom.cdx.json" --repo StaffOps/aigent-squad

# Or view from the registry
cosign tree ghcr.io/staffops/aigent-squad@${DIGEST}
```
