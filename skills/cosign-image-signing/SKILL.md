---
name: cosign-image-signing
description: Image signing with cosign at <org>. Use when configuring image signing pipelines, debugging Harbor signature visibility, key rotation, or verifying signed images. Covers cosign v3 vs v2 differences, --new-bundle-format=false fix, key management in AWS Secrets Manager, ECDSA key pair.
keywords: [cosign-image-signing, cosign, image, signing, "cosign image", "image signing", harbor, aws, ecdsa]
---
# Cosign Image Signing

Image signing patterns for <org>'s Harbor registry.

> **Scope**: cosign signing applies **only to golden/base images** (apko-built, in `<harbor-project>/*`, used in `FROM`). Application images that derive from a signed golden base are NOT signed individually — they inherit trust. The Kyverno policy below verifies the golden base, not every app image.

## CRITICAL: Cosign v3 default breaks Harbor visibility

### Problem

Images signed with cosign v3.0+ don't show signatures in Harbor UI.

### Root cause

Cosign v3 (released October 2025) changed defaults:

| Default flag | v2 | v3 |
|--------------|----|----|
| `--new-bundle-format` | false | **true** |
| `--use-signing-config` | false | **true** |

With `--new-bundle-format=true`, cosign stores signatures as **OCI Referrers** (Referrers API). Harbor doesn't support the Referrers API for signature display.

### Signature format comparison

| Format | Type field | Bundle/Rekor | Harbor shows? |
|--------|-----------|--------------|---------------|
| Legacy (v2 default) | `cosign container image signature` | ✅ Inline bundle | ✅ Yes |
| New (v3 default) | `sigstore.dev/cosign/sign/v1` | ❌ No inline bundle | ❌ No |

### Fix

Always pass `--new-bundle-format=false` to sign commands:

```bash
cosign sign --key cosign.key --new-bundle-format=false ${DIGEST} -y
```

For build scripts, update `build-imgs.sh`:

```bash
COSIGN_PASSWORD=$COSIGN_PASSWORD cosign sign \
  --key cosign.key \
  --new-bundle-format=false \
  ${DIGEST} -y
```

## Cosign installation at <org>

- **Version**: v3.0.6 (built 2026-04-06)
- **Location**: `<workspace>/01-DEVOPS/AUTOMATIONS/CONTAINER/custom-images/`

## Key pair management

### Algorithm
ECDSA P-256 (prime256v1), 256 bits.

### Files
- `cosign.key` — private key (ENCRYPTED with password)
- `cosign.pub` — public key (distributed for verification)

### Password storage
AWS Secrets Manager:
- Secret: `DEVOPS_AUTOMATION_SECRETS`
- Key: `COSIGN_PASSWORD`

### Validate key pair

Extract public key from private and compare:
```bash
COSIGN_PASSWORD=$COSIGN_PASSWORD cosign public-key --key cosign.key > /tmp/derived.pub
diff /tmp/derived.pub cosign.pub
# Should produce no output if keys match
```

## Key rotation

### Important
- **Cosign keys don't have built-in expiration dates** — rotation is manual/policy-based
- Rotation does NOT invalidate previously signed images (signatures verify against the public key embedded at sign time)

### Rotation process

1. Generate new key pair:
   ```bash
   cosign generate-key-pair
   # Generates cosign.key.new + cosign.pub.new
   # Asks for new password
   ```
2. Update password in AWS Secrets Manager (new entry, keep old one for verification of legacy signatures)
3. Update CI/CD to use new key
4. Distribute new public key (`cosign.pub.new` → `cosign.pub`)
5. Document the rotation date and old key location (for future signature verification)

### Recommended cadence
- **Annual rotation** — security best practice
- **Immediate rotation** — if compromise suspected

## Sign command (full)

```bash
COSIGN_PASSWORD=$(aws secretsmanager get-secret-value \
  --secret-id DEVOPS_AUTOMATION_SECRETS \
  --query 'SecretString' --output text | jq -r '.COSIGN_PASSWORD')

cosign sign \
  --key cosign.key \
  --new-bundle-format=false \  # CRITICAL for Harbor
  <harbor-registry>/<harbor-project>/<IMAGE>:<TAG> \
  -y
```

The `-y` flag auto-confirms (avoids interactive prompt).

## Verify command

```bash
cosign verify \
  --key cosign.pub \
  <harbor-registry>/<harbor-project>/<IMAGE>:<TAG>
```

Output (when valid):
```json
[{
  "critical": {
    "identity": {
      "docker-reference": "<harbor-registry>/<harbor-project>/myapp"
    },
    "image": {
      "docker-manifest-digest": "sha256:..."
    },
    "type": "cosign container image signature"
  },
  ...
}]
```

The `"type": "cosign container image signature"` confirms legacy format (v2-compatible).

## CI/CD pipeline integration

### GitLab CI snippet

```yaml
sign:
  stage: sign
  image: bitnami/cosign:3.0.6
  script:
    - aws secretsmanager get-secret-value
        --secret-id DEVOPS_AUTOMATION_SECRETS
        --query SecretString --output text | jq -r '.COSIGN_PASSWORD' > /tmp/cosign.pwd
    - export COSIGN_PASSWORD=$(cat /tmp/cosign.pwd)
    - DIGEST=$(crane digest <harbor-registry>/$PROJECT/$IMAGE:$CI_COMMIT_SHORT_SHA)
    - cosign sign
        --key $COSIGN_KEY_PATH
        --new-bundle-format=false
        <harbor-registry>/$PROJECT/$IMAGE@$DIGEST -y
  only:
    - main
```

### Pre-flight checks

Before signing:
1. Image must exist in Harbor (`docker pull` or `crane manifest`)
2. Use `crane digest` to get the immutable SHA (don't sign tags — signatures bind to digests)
3. Verify cosign version: `cosign version` should show 3.0.x

## Harbor verification UI

After signing with `--new-bundle-format=false`:

1. Navigate to Harbor → Project → Repository → Tag list
2. Each signed tag shows a "Signed" indicator
3. Click on the tag → Signatures section shows:
   - Public key (or fingerprint)
   - Signature timestamp
   - Type: `cosign container image signature`

## Admission policy enforcement

To require signed golden/base images (the policy targets `<harbor-project>/*`, not every app image):

```yaml
# Kyverno ClusterPolicy example
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: verify-<harbor-project>
spec:
  validationFailureAction: Enforce
  rules:
    - name: check-signature
      match:
        any:
          - resources:
              kinds: [Pod]
      verifyImages:
        - imageReferences:
            - "<harbor-registry>/<harbor-project>/*"
          attestors:
            - entries:
                - keys:
                    publicKeys: |-
                      -----BEGIN PUBLIC KEY-----
                      <cosign.pub content>
                      -----END PUBLIC KEY-----
```

## Common issues

### Issue: Harbor doesn't show signatures
Cause: signed with v3 default (`--new-bundle-format=true`).
Fix: re-sign with `--new-bundle-format=false`.

### Issue: `cosign verify` fails with "no matching signatures"
Possible causes:
- Image not signed
- Signed with different key (look at signed digest, verify with that key)
- Signature stored as Referrers (Harbor invisible) — try `cosign verify --bundle...`

### Issue: `decryption failed` during sign
Cause: wrong password.
Fix: verify `COSIGN_PASSWORD` env var; re-fetch from AWS Secrets Manager.

### Issue: signing fails with `unable to read key file`
Cause: file path wrong, or missing read permissions.
Fix: ensure CI runner has access; use absolute paths; check file exists.

### Issue: signatures pile up after re-signs
Cause: each `cosign sign` adds a new signature (cosign supports multiple signatures per image).
Mitigation: use `cosign tag-image` to remove old signatures, OR delete via Harbor API. Usually multiple signatures are fine.

## Image signing best practices

### Always sign by digest, not tag
```bash
# ❌ Bad: tag is mutable
cosign sign --key cosign.key <harbor-registry>/myapp:v1.0.0

# ✅ Good: digest is immutable
DIGEST=$(crane digest <harbor-registry>/myapp:v1.0.0)
cosign sign --key cosign.key <harbor-registry>/myapp@$DIGEST
```

### Sign in CI, not manually
- Avoids password leakage
- Auditable (CI logs show who/when)
- Consistent

### Verify in deployment pipeline
Before deploying, verify the signature:
```bash
cosign verify --key cosign.pub $IMAGE || exit 1
```

## Reference

- Cosign docs: https://docs.sigstore.dev/cosign/overview/
- Sigstore: https://www.sigstore.dev/
- Harbor signature integration: https://goharbor.io/docs/latest/working-with-projects/working-with-images/signing-images/
- Related: `ci-cd-conventions` (steering — golden/base image signing is mandatory)
