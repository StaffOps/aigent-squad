---
name: container-package-melange
description: "Use when building custom APK packages with Chainguard melange, creating Alpine/Wolfi packages for apko images, or managing the <org> custom package repository. Covers melange.yaml structure, multi-arch builds, signing keys, S3 publishing, apko integration, and <org>-specific patterns."
keywords: [container-package-melange, container, package, melange, "container package", "package melange", apko, s3, apk]
---
# Container Package Builds with melange (Chainguard)

melange builds reproducible Alpine APK packages from declarative YAML. <org> uses melange for custom tools and patched libraries consumed by apko golden images.

## Location

**Path**: `<workspace>/01-DEVOPS/AUTOMATIONS/CONTAINER/custom-packages/`

## Why melange

| Aspect | Traditional APKBUILD | melange |
|--------|---------------------|---------|
| Format | Shell script (APKBUILD) | Declarative YAML |
| Multi-arch | Manual cross-compile setup | Native (`archs:` field) |
| Reproducibility | Depends on build env | Fully reproducible |
| Pipeline steps | Implicit (prepare/build/install) | Explicit pipeline steps |
| Signing | Manual `abuild-sign` | Built-in (`--signing-key`) |
| Integration | Standalone | Designed for apko consumption |

## melange.yaml structure

```yaml
package:
  name: <org>-custom-tool
  version: 1.3.2
  epoch: 0
  description: "Internal CLI tool for <org> operations"
  copyright:
    - license: Apache-2.0
  dependencies:
    runtime:
      - ca-certificates-bundle
      - libgcc

environment:
  contents:
    repositories:
      - https://dl-cdn.alpinelinux.org/alpine/v3.20/main
      - https://dl-cdn.alpinelinux.org/alpine/v3.20/community
    packages:
      - build-base
      - go
      - ca-certificates-bundle

pipeline:
  - uses: fetch
    with:
      uri: https://github.com/org/tool/archive/v${{package.version}}.tar.gz
      expected-sha256: a1b2c3d4e5f6...

  - uses: go/build
    with:
      packages: ./cmd/tool
      output: <org>-custom-tool
      ldflags: "-s -w -X main.version=${{package.version}}"

  - uses: strip
```

### Key sections

| Section | Purpose |
|---------|---------|
| `package` | Name, version, epoch, license, runtime deps |
| `environment` | Build-time repos and packages (not in final APK) |
| `pipeline` | Ordered build steps (fetch, compile, install) |
| `subpackages` | Split package into multiple APKs (e.g., `-doc`, `-dev`) |

## Pipeline steps (built-in)

| Step | Purpose | Example |
|------|---------|---------|
| `fetch` | Download source tarball | `uri` + `expected-sha256` |
| `go/build` | Build Go binary | `packages`, `output`, `ldflags` |
| `cmake/build` | CMake project | `build-dir`, `opts` |
| `meson/build` | Meson project | `build-dir`, `opts` |
| `autoconf/configure` | Autotools | `opts` |
| `make/build` | GNU Make | `targets`, `opts` |
| `strip` | Strip debug symbols | (no args) |
| `run` | Arbitrary shell commands | `command` |

### Custom pipeline step (shell)

```yaml
pipeline:
  - uses: fetch
    with:
      uri: https://example.com/source-${{package.version}}.tar.gz
      expected-sha256: abc123...

  - runs: |
      cd source-${{package.version}}
      make PREFIX=/usr install DESTDIR=${{targets.destdir}}

  - uses: strip
```

## Multi-arch builds

melange builds for multiple architectures natively:

```bash
# Build for both amd64 and arm64
melange build melange.yaml \
  --arch amd64,arm64 \
  --signing-key melange.rsa \
  --out-dir ./packages/
```

Output structure:
```
packages/
├── amd64/
│   └── <org>-custom-tool-1.3.2-r0.apk
└── arm64/
    └── <org>-custom-tool-1.3.2-r0.apk
```

## Signing keys

### Generate key pair

```bash
melange keygen melange.rsa
# Produces: melange.rsa (private) + melange.rsa.pub (public)
```

### Key storage

| Asset | Location |
|-------|----------|
| Private key (`melange.rsa`) | AWS Secrets Manager: `DEVOPS_AUTOMATION_SECRETS` → `MELANGE_SIGNING_KEY` |
| Public key (`melange.rsa.pub`) | Repo root (safe to commit) + S3 repo metadata |

### Sign during build

```bash
melange build melange.yaml \
  --signing-key melange.rsa \
  --arch amd64,arm64 \
  --out-dir ./packages/
```

### Verify signature

```bash
# apk verifies automatically when public key is in keyring
apk --keys-dir ./keys/ verify packages/amd64/<org>-custom-tool-1.3.2-r0.apk
```

## Integration with apko

melange packages are consumed by apko via local or remote repository:

### apko.yaml consuming melange packages

```yaml
contents:
  repositories:
    - https://dl-cdn.alpinelinux.org/alpine/v3.20/main
    - https://devops-files.<org-domain>/apk-repo/edge/  # <org> melange packages
    - '@local /packages'  # Local build output (CI)
  keyring:
    - https://dl-cdn.alpinelinux.org/alpine/alpine-keys/alpine-keys.rsa.pub
    - https://devops-files.<org-domain>/apk-repo/edge/<org>.rsa.pub  # <org> key
  packages:
    - ca-certificates-bundle
    - <org>-custom-tool  # melange-built package
```

### CI flow: melange → apko

```yaml
# .gitlab-ci.yml
stages:
  - package
  - image

build:apk:
  stage: package
  script:
    - melange build melange.yaml
        --signing-key ${MELANGE_KEY_PATH}
        --arch amd64,arm64
        --out-dir ./packages/
  artifacts:
    paths: [packages/]

build:image:
  stage: image
  needs: [build:apk]
  script:
    - apko build apko.yaml ${IMAGE}:${TAG} image.tar
    - crane push image.tar ${IMAGE}:${TAG}
    - cosign sign --key ${COSIGN_KEY} --new-bundle-format=false ${IMAGE}@${DIGEST} -y
```

## Publishing to S3

APKs are published to the DevOps S3 bucket via `upload_apk_nexus.sh` (legacy name):

```bash
# Pipeline builds both archs and uploads to S3
# The script handles: build (melange) → sign → upload to S3 bucket
# Endpoint: https://devops-files.<org-domain>/apk-repo/edge/
```

## Trigger mapping (custom-packages → custom-images)

When a melange package changes, the CI automatically triggers the downstream `custom-images` pipeline to rebuild only the affected images:

| If you change... | Triggers image rebuild... | `APK_BUILD_UPDATE` value |
|------------------|--------------------------|--------------------------|
| `dotnetN-asp.yaml` | `dotnet-aspnet-N` | `ASPNETN` |
| `dotnetN-runtime.yaml` | `dotnet-runtime-N` | `RUNTIMEN` |
| `dotnetN-builder.yaml` | `dotnet-builder-N` | `BUILDERN` |
| `<org>-scripts/**` | `<org>-runner` + all builders | `<org>-RUNNER` / `BUILDERN` |
| `<org>-cert.yaml` | **All images** (shared) | Multiple triggers |
| `<org>-entrypoint-asp.yaml` | All `dotnet-aspnet-*` | `ASPNET6/8/10` |
| `<org>-entrypoint-run.yaml` | All `dotnet-runtime-*` | `RUNTIME6/8/10` |
| `<org>-entrypoint-devops.yaml` | `devops-runner` | `DEVOPS-RUNNER` |
| `security-*.yaml` | `secret-scanner` | `SECURITY-SCANNER` |
| `devops-scripts/**` | `devops-runner` | `DEVOPS-RUNNER` |
| `helm-plugins.yaml` | `devops-runner` | `DEVOPS-RUNNER` |

## Version management

### Epoch for rebuilds

When rebuilding the same version (e.g., security patch to build deps):

```yaml
package:
  name: <org>-tool
  version: 1.3.2
  epoch: 1  # Increment epoch, not version
```

`epoch` takes precedence over version in APK ordering: `1.3.2-r1` > `1.3.2-r0`.

### Version ranges in apko

```yaml
# apko.yaml — pin to major.minor, allow patch updates
contents:
  packages:
    - <org>-custom-tool~1.3  # Matches 1.3.x
```

## Subpackages

Split large packages into components:

```yaml
package:
  name: <org>-sdk
  version: 2.0.0

subpackages:
  - name: <org>-sdk-dev
    description: "Development headers"
    pipeline:
      - runs: |
          mkdir -p ${{targets.subpkgdir}}/usr/include
          mv ${{targets.destdir}}/usr/include/* ${{targets.subpkgdir}}/usr/include/

  - name: <org>-sdk-doc
    description: "Documentation"
    pipeline:
      - runs: |
          mkdir -p ${{targets.subpkgdir}}/usr/share/doc
          mv ${{targets.destdir}}/usr/share/doc/* ${{targets.subpkgdir}}/usr/share/doc/
```

## <org> packages catalog

| Package | Purpose | Consumed by |
|---------|---------|-------------|
| `<org>-runner-tools` | CI runner utilities (kubectl, helm, aws-cli) | `<org>-runner` apko image |
| `dotnet-*-runtime` | .NET runtime packages | `dotnet-aspnet-*` apko images |
| `<org>-otel-contrib` | OTel Collector contrib binary | Collector image |
| `<org>-cosign` | cosign binary (pinned version) | CI runner images |

## Complete example: Go CLI tool

```yaml
# melange.yaml
package:
  name: <org>-deploy-tool
  version: 0.5.0
  epoch: 0
  description: "<org> deployment helper CLI"
  copyright:
    - license: Apache-2.0
  dependencies:
    runtime:
      - ca-certificates-bundle

environment:
  contents:
    repositories:
      - https://dl-cdn.alpinelinux.org/alpine/v3.20/main
      - https://dl-cdn.alpinelinux.org/alpine/v3.20/community
    packages:
      - build-base
      - go~1.22
      - ca-certificates-bundle
      - git

pipeline:
  - uses: fetch
    with:
      uri: https://gitlab.<old-internal-domain>/devops/deploy-tool/-/archive/v${{package.version}}/deploy-tool-v${{package.version}}.tar.gz
      expected-sha256: deadbeef...

  - uses: go/build
    with:
      packages: ./cmd/deploy
      output: <org>-deploy
      ldflags: "-s -w -X main.version=${{package.version}} -X main.commit=${{package.epoch}}"

  - uses: strip
```

Build and publish:
```bash
# Build
melange build melange.yaml --signing-key melange.rsa --arch amd64,arm64 --out-dir ./packages/

# Publish
for ARCH in amd64 arm64; do
  curl --upload-file "${APKINDEX}" \
    --upload-file "packages/${ARCH}/<org>-deploy-tool-0.5.0-r0.apk" \
    "https://devops-files.<org-domain>/apk-repo/edge/${ARCH}/"
done
```

## Anti-patterns

- ❌ Building system-level deps (openssl, zlib) instead of using Wolfi/Alpine APKs
- ❌ Hardcoded version without epoch management (can't rebuild for security patches)
- ❌ Missing `expected-sha256` in fetch step (supply chain risk)
- ❌ Single-arch builds (breaks Graviton scheduling)
- ❌ Storing signing key in git (use AWS Secrets Manager)
- ❌ No APKINDEX upload to S3 (apk can't resolve package)
- ❌ Using `runs: apk add <pkg>` in pipeline instead of `environment.contents.packages`
- ❌ Packages without `strip` step (unnecessarily large APKs)
- ❌ Runtime dependencies in `environment` instead of `package.dependencies.runtime`
- ❌ Version pinning without `~` operator in apko (breaks patch updates)
- ❌ Manual builds without CI pipeline (no reproducibility guarantee)
- ❌ Skipping signature verification in apko keyring (unsigned packages accepted)

## Related

- `container-image-apko` skill — apko consumes melange packages
- `cosign-image-signing` skill — final image signing after apko build
- `ci-cd-conventions` steering — multi-arch build requirements
- `multi-arch-builds` steering — architecture support mandate
- Path: `<workspace>/01-DEVOPS/AUTOMATIONS/CONTAINER/custom-packages/`
