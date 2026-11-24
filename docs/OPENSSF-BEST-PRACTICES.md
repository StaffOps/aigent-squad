# OpenSSF Best Practices — passing-level evidence map

Preparation for registering `StaffOps/aigent-squad` at <https://www.bestpractices.dev/> (the
OpenSSF Best Practices badge, formerly CII). The OpenSSF Scorecard `CII-Best-Practices` check
scores 0 until the project is **registered** there: it only reads the badge status from the
bestpractices.dev API, so nothing in the repository can earn it. The registration is a self-assessed
questionnaire; this file maps each **passing** criterion to the evidence to paste, so the form takes
minutes rather than an afternoon, and lists what is not met yet.

Status legend: **Met** (evidence exists), **N/A** (criterion does not apply, with the reason the form
asks for), **Gap** (must be fixed before claiming passing), **Owner** (a fact only the maintainer
can assert).

Snapshot date: 2026-11-24. Re-check the gaps before submitting.

## Gaps to close before submitting

| Criterion | Why it is a gap | Fix |
|-----------|-----------------|-----|
| `floss_license_osi` / `license_location` (MUST) | `LICENSE` is titled Apache-2.0 but its text differs from the canonical license in ~115 lines (several definitions in section 1 are reworded or truncated). GitHub reports the license as `NOASSERTION` and Scorecard says "license file does not contain an FSF or OSI license". A reworded license is legally not Apache-2.0. | Replace `LICENSE` with the verbatim text from <https://www.apache.org/licenses/LICENSE-2.0.txt> (keep the copyright notice in the appendix). Owner decision — it is the legal grant. |
| `vulnerabilities_fixed_60_days` (MUST) | The 2026-11-23 Scorecard run lists 23 open OSV advisories against declared dependencies (`Vulnerabilities` = 0). Any medium+ one public for more than 60 days fails this criterion. | Triage the list (`trivy fs` / `make scan-fs`, or the OSV links in the Scorecard result): upgrade, or record a justified non-exploitability exception in `.trivyignore.yaml`. |
| `release_notes_vulns` (MUST) | Release notes must name every publicly known vulnerability fixed in that release. `CHANGELOG.md` has no convention for it. | Add a `### Security` subsection (Keep a Changelog) listing CVE/GHSA ids per release, starting with the next one. |

## Basics

| Criterion | Status | Evidence |
|-----------|--------|----------|
| `description_good` | Met | `README.md` → Overview |
| `interact` | Met | `README.md`, `CONTRIBUTING.md`; GitHub Issues enabled |
| `contribution` / `contribution_requirements` | Met | `CONTRIBUTING.md` (spec-driven flow, `make` targets, coverage gate, conventional commits) |
| `floss_license` / `floss_license_osi` / `license_location` | **Gap** | see above |
| `documentation_basics` | Met | `README.md`, `QUICKSTART.md`, `docs/SETUP.md`, published site <https://staffops.github.io/aigent-squad/> |
| `documentation_interface` | Met | `openapi/gateway.json`, `openapi/supervisor.json` (CI fails if stale), `docs/ARCHITECTURE.md`, `docs/HOW-TO-NEW-AGENT.md` |
| `sites_https` | Met | GitHub, GHCR and the docs site are HTTPS-only |
| `discussion` | Met | GitHub Issues and pull requests (searchable, URL-addressable) |
| `english` | Met | All docs and code comments are in English |
| `maintained` | Owner | Assert actively maintained (commit activity on `dev`/`main`) |

## Change control

| Criterion | Status | Evidence |
|-----------|--------|----------|
| `repo_public` / `repo_track` / `repo_interim` / `repo_distributed` | Met | Public Git repository on GitHub; work happens on `dev`, interim commits are public |
| `version_unique` / `version_semver` / `version_tags` | Met | SemVer tags `v0.2.0` … `v0.5.0`; `RELEASE.md` |
| `release_notes` | Met | `CHANGELOG.md` (Keep a Changelog) + generated GitHub Release notes |
| `release_notes_vulns` | **Gap** | see above |

## Reporting

| Criterion | Status | Evidence |
|-----------|--------|----------|
| `report_process` / `report_tracker` / `report_archive` | Met | GitHub Issues (public, searchable archive) |
| `report_responses` / `enhancement_responses` | Owner | Assert that most reports/enhancement requests in the last 12 months got a response |
| `vulnerability_report_process` | Met | `.github/SECURITY.md` |
| `vulnerability_report_private` | Met | GitHub Private Vulnerability Reporting, linked from `.github/SECURITY.md` |
| `vulnerability_report_response` | Met | `.github/SECURITY.md` → "What to expect" (acknowledgement target 3 business days, within the 14-day MUST) |

## Quality

| Criterion | Status | Evidence |
|-----------|--------|----------|
| `build` / `build_common_tools` / `build_floss_tools` | Met | `Makefile`, `Dockerfile`, Docker Buildx — all FLOSS |
| `test` / `test_invocation` / `test_most` | Met | `make test` (pytest), ≥90% line-coverage gate (`.coveragerc`, `make test-ci`) |
| `test_continuous_integration` | Met | `.github/workflows/test.yml` on every push/PR to `dev`/`main` |
| `test_policy` / `tests_are_added` / `tests_documented_added` | Met | `AGENTS.md` → Workflow rules ("Tests ship with code: ≥90% coverage, independent author"), `CONTRIBUTING.md` |
| `warnings` / `warnings_fixed` | Met | `make lint` (ruff, `ruff.toml`) and `make typecheck` (mypy) are CI gates that block the test job |
| `warnings_strict` (SUGGESTED) | Partial | ruff is strict and gating; mypy is a gate but not full `strict` (~184 known missing-annotation errors outside the enabled checks — `AGENTS.md` → Typecheck gate) |

## Security

| Criterion | Status | Evidence |
|-----------|--------|----------|
| `know_secure_design` / `know_common_errors` | Owner | Assert; supporting evidence: `docs/SECURITY.md` (threat model), spec 14 (prompt-injection defense in depth), spec 45 (supply chain) |
| `crypto_published` / `crypto_call` / `crypto_floss` / `crypto_keylength` / `crypto_working` / `crypto_weaknesses` / `crypto_pfs` | N/A | The project implements no cryptography; TLS and signing come from standard FLOSS libraries/tools (Python `ssl`, boto3, cosign) |
| `crypto_password_storage` | N/A | No end-user passwords are stored; API tokens are compared, never persisted by the project |
| `crypto_random` | Met | Security-sensitive random values come from the OS CSPRNG: the L5 canary token uses `secrets.token_hex` (`src/core/canary.py`); job ids (the capability for `/jobs/{id}/cancel`, `src/gateway/main.py`) and session-lock tokens (`src/core/session_lock.py`) use `uuid.uuid4()`, which is `os.urandom`-backed |
| `delivery_mitm` | Met | Delivered over HTTPS (GitHub, GHCR); images signed with cosign and carry build provenance — `docs/VERIFYING-RELEASES.md` |
| `delivery_unsigned` | Met | No hash is fetched over HTTP and trusted; verification is signature-based |
| `vulnerabilities_fixed_60_days` | **Gap** | see above |
| `vulnerabilities_critical_fixed` (SHOULD) | Met | Trivy gates every image push on CRITICAL/HIGH (`build.yml`, `release.yml`) |
| `no_leaked_credentials` | Met | No credentials in the repository; secrets come from GitHub Actions secrets / mounted files (`docs/SECURITY.md`) |

## Analysis

| Criterion | Status | Evidence |
|-----------|--------|----------|
| `static_analysis` / `static_analysis_often` | Met | Bandit (`sast.yml`), CodeQL python + actions (`codeql.yml`), ruff, mypy — on every push/PR |
| `static_analysis_common_vulnerabilities` | Met | Bandit and CodeQL `security-extended` target common vulnerability classes |
| `static_analysis_fixed` (SHOULD) | Met | Bandit fails the job on findings; reviewed false positives carry `# nosec <ID>` with a reason |
| `dynamic_analysis` (SUGGESTED) | Partial | `stack.yml` boots the full stack and probes `/ready` and edge auth; no fuzzing yet (proposed under BACKLOG B-33) |
| `dynamic_analysis_unsafe` | N/A | Python is memory-safe |
| `dynamic_analysis_enable_assertions` / `dynamic_analysis_fixed` | Met | Test suite runs with assertions enabled; failures block merge |

## After registering

1. Add the badge to `README.md` next to the Scorecard badge (bestpractices.dev gives the Markdown).
2. Scorecard picks the badge up on its next weekly run (`scorecard.yml`): `CII-Best-Practices`
   becomes 5 for *passing* (`in_progress` scores 2).
