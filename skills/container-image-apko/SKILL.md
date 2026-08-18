---
name: container-image-apko
description: "Chainguard apko container image build patterns. Use when building hardened base images, creating minimal container images, or working with the <org> golden image catalog. Covers apko.yaml structure, multi-arch builds, SBOM inclusion, melange integration, and <org>-specific image patterns."
keywords: [container-image-apko, container, image, apko, "container image", "image apko", melange, sbom]
---
# Container Image Builds with apko (Chainguard)

<org> uses **apko** for all golden/base container images. Declarative, minimal, multi-arch, SBOM-included.

## Why apko over Dockerfile

| Aspect | Dockerfile | apko |
|--------|-----------|------|
| Approach | Imperative (RUN commands) | Declarative (YAML) |
| Attack surface | Shell, package manager in image | No shell, no pkg manager |
| Multi-arch | Requires buildx + TARGETPLATFORM | Native (list archs) |
| SBOM | Manual (trivy, syft) | Automatic (CycloneDX) |
| Reproducibility | Layer caching issues | Fully reproducible |
| Image size | Larger (layers, cache) | Minimal (only packages) |

## Locations

| Path | Purpose |
|------|---------|
| `<workspace>/01-DEVOPS/AUTOMATIONS/CONTAINER/custom-images/` | apko image definitions |
| `<workspace>/01-DEVOPS/AUTOMATIONS/CONTAINER/custom-packages/` | melange APK packages |

## apko.yaml structure

```yaml
contents:
  repositories:
    - https://dl-cdn.alpinelinux.org/alpine/v3.20/main
    - https://dl-cdn.alpinelinux.org/alpine/v3.20/community
    - https://devops-files.<org-domain>/apk-repo/edge/  # <org> custom APKs
  keyring:
    - https://dl-cdn.alpinelinux.org/alpine/alpine-keys/alpine-keys.rsa.pub
  packages:
    - ca-certificates-bundle
    - dotnet-8-aspnet-runtime  # example: .NET runtime
    - tzdata

entrypoint:
  command: /usr/bin/dotnet

accounts:
  run-as: 65534  # nobody (non-root)
  users:
    - username: nonroot
      uid: 65534
  groups:
    - groupname: nonroot
      gid: 65534

archs:
  - amd64
  - arm64

environment:
  DOTNET_RUNNING_IN_CONTAINER: "true"
  ASPNETCORE_URLS: "http://+:8080"
```

## Multi-arch builds

apko handles multi-arch natively — just list architectures:

```yaml
archs:
  - amd64
  - arm64
```

Build command:
```bash
apko build apko.yaml <harbor-registry>/<harbor-project>/my-image:v1.0.0 image.tar
```

Produces a multi-arch OCI image with both platforms.

## melange — Custom APK packages

When you need packages not in Alpine repos, build them with melange:

```yaml
# melange.yaml
package:
  name: <org>-custom-tool
  version: 1.2.3
  description: Custom internal tool
  copyright:
    - license: Apache-2.0

environment:
  contents:
    repositories:
      - https://dl-cdn.alpinelinux.org/alpine/v3.20/main
    packages:
      - build-base
      - go

pipeline:
  - uses: fetch
    with:
      uri: https://github.com/org/tool/archive/v${{package.version}}.tar.gz
      expected-sha256: abc123...
  - uses: go/build
    with:
      packages: ./cmd/tool
      output: <org>-custom-tool
  - uses: strip
```

Build:
```bash
melange build melange.yaml --arch amd64,arm64
```

Publish to S3 (handled by CI pipeline automatically):
```bash
# Upload APK to S3 APK repository
# Script: upload_apk_nexus.sh (legacy name, now targets S3)
# Endpoint: https://devops-files.<org-domain>/apk-repo/edge/x86_64/
```

## <org> golden images catalog

| Image | Base | Purpose |
|-------|------|---------|
| `<org>-runner` | Alpine 3.20 | GitLab CI runner (Docker, kubectl, helm, aws-cli) |
| `dotnet-builder-6` | Alpine + .NET SDK 6 | Build .NET 6 apps |
| `dotnet-builder-8` | Alpine + .NET SDK 8 | Build .NET 8 apps |
| `dotnet-builder-10` | Alpine + .NET SDK 10 | Build .NET 10 apps |
| `dotnet-aspnet-8` | Alpine + ASP.NET 8 runtime | Run .NET 8 apps |
| `dotnet-aspnet-10` | Alpine + ASP.NET 10 runtime | Run .NET 10 apps |
| `python-3.11` | Alpine + Python 3.11 | Run Python apps (**NOT 3.12**) |
| `golang-1.22` | Alpine + Go 1.22 | Build Go apps |

Registry: `<harbor-registry>/<harbor-project>/<name>:<version>`

## CI pipeline

The `custom-images` repo has a 4-stage pipeline:

```
build-img → test → manifest → test-pos
```

| Stage | What it does |
|-------|-------------|
| **build-img** | `apko build` per arch → push to Harbor → cosign sign each |
| **test** | Validates binaries, versions, entrypoint work |
| **manifest** | Creates multi-arch manifest (`latest`) → cosign sign manifest |
| **test-pos** | Final validation (pulls `latest`, verifies integrity) |

### Triggered by custom-packages

When a melange package changes in `custom-packages`, it triggers `custom-images` via GitLab CI with `APK_BUILD_UPDATE=<IMAGE_NAME>`. Only the affected images rebuild.

### Pipeline example (simplified)

```yaml
# .gitlab-ci.yml (custom-images)
.apko-build:
  stage: build-img
  script:
    - bash build-imgs.sh  # apko build + docker push + cosign sign

.manifests:
  stage: manifest
  script:
    - bash manifests.sh  # multi-arch manifest + cosign sign manifest
```

### build-imgs.sh flow

```bash
# 1. apko build per arch
apko build apko-tpl/${FOLDER}/${IMAGE_NAME}.yaml ${REPO}/${IMAGE_NAME}:temp image.tar --arch arm64
apko build apko-tpl/${FOLDER}/${IMAGE_NAME}.yaml ${REPO}/${IMAGE_NAME}:temp image.tar --arch amd64

# 2. Push to Harbor
docker push --all-tags ${REPO}/${IMAGE_NAME}

# 3. Cosign sign each arch
COSIGN_PASSWORD=$COSIGN_PASSWORD cosign sign --key cosign.key --new-bundle-format=false ${DIGEST} -y
```

### manifests.sh flow

```bash
# 1. Retag temp → latest per arch
docker tag ${REPO}/${IMAGE_NAME}:temp-amd64 ${REPO}/${IMAGE_NAME}:latest-amd64
docker tag ${REPO}/${IMAGE_NAME}:temp-arm64 ${REPO}/${IMAGE_NAME}:latest-arm64

# 2. Create multi-arch manifest
docker manifest create ${REPO}/${IMAGE_NAME}:latest \
  ${REPO}/${IMAGE_NAME}:latest-amd64 \
  ${REPO}/${IMAGE_NAME}:latest-arm64

# 3. Push + cosign sign manifest
docker manifest push ${REPO}/${IMAGE_NAME}:latest
cosign sign --key cosign.key --new-bundle-format=false ${REPO}/${IMAGE_NAME}@${MANIFEST_DIGEST} -y
```

## Rebuild schedule

Golden images are rebuilt **weekly** (cron pipeline) to pick up:
- Alpine security patches
- .NET/Python/Go runtime updates
- Custom package updates from melange

## Security properties

- ✅ No shell (`/bin/sh` not included) — prevents RCE escalation
- ✅ No package manager — prevents runtime package installation
- ✅ Non-root by default (UID 65534)
- ✅ Read-only rootfs compatible
- ✅ SBOM attached to image manifest
- ✅ Signed with cosign (Kyverno verifies)
- ✅ Minimal packages (only what's needed)

## Anti-patterns

- ❌ Adding shell to apko images ("for debugging") — defeats security purpose
- ❌ Using Dockerfile when apko suffices (most services can use apko)
- ❌ Skipping SBOM generation (compliance requirement)
- ❌ Single-arch builds (breaks Graviton scheduling)
- ❌ Not rebuilding weekly (stale CVEs accumulate)
- ❌ Using upstream Docker Hub images directly (use Harbor proxy + golden images)
- ❌ Python 3.12 in golden images (pkg_resources breaks OTel instrumentations)
- ❌ Running as root in apko images (always set run-as: 65534)

## When to use Dockerfile instead

apko is preferred, but Dockerfile is acceptable when:
- Application needs runtime compilation (JIT, native extensions)
- Complex multi-stage build with intermediate artifacts
- Third-party software with specific installation requirements
- Development/debug images (where shell is intentionally needed)

Even then, use an apko-built base image as the final stage.
