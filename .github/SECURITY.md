# Security Policy

This file is the **vulnerability disclosure policy**: how to report a problem and what to expect.

It is deliberately *not* the security architecture. For the threat model — authentication between
components, container hardening, secret handling, the read-only enforcement layers — see
[`docs/SECURITY.md`](../docs/SECURITY.md). For verifying that a released image is authentic, see
`docs/VERIFYING-RELEASES.md` (arriving with spec 45 Phase 3).

## Supported versions

`aigent-squad` is pre-1.0 and moves fast. Only the **latest released version** receives security
fixes. There are no backports to older tags.

| Version | Supported |
|---------|-----------|
| latest release (`v0.x`, highest tag) | ✅ |
| any earlier tag | ❌ — upgrade |
| `latest` / commit-SHA images from `main` | ⚠️ built continuously, not a release; unsigned by design |

## Reporting a vulnerability

**Use GitHub Private Vulnerability Reporting** — the "Report a vulnerability" button under the
repository's **Security** tab. It keeps the report private until a fix exists and gives us a place to
collaborate with you on the details.

Please do **not** open a public issue for a security problem, and do not disclose it publicly before
a fix is available.

If private reporting is unavailable to you for any reason, open a regular issue containing **only**
the sentence "I need to report a security issue privately" and nothing else — no details — and a
maintainer will arrange a private channel.

### What to include

Anything that helps us reproduce it: affected version or image digest, the component
(gateway / supervisor / an agent / the MCP layer), a minimal reproduction, and the impact you
believe it has. A partial report is still worth sending — we would rather triage an incomplete report
than never hear about it.

## What to expect

The project is maintained by a very small team, so these are honest targets rather than a contractual
SLA:

| Stage | Target |
|-------|--------|
| Acknowledgement that we received it | 3 business days |
| Initial assessment (is it a vulnerability, and how severe) | 10 business days |
| Fix or a documented mitigation for high/critical | 30 days from assessment |
| Public disclosure | after a fix ships, coordinated with you |

If a report turns out not to be a vulnerability we will say so and explain why, rather than closing
it silently.

## Scope

**In scope**: this repository — the gateway, supervisor, agents, MCP integration layer, the Helm
chart and Terraform under `infra/`, the published container image, and the CI/CD workflows
(including the supply-chain surface: action pinning, credential handling, release signing).

**Out of scope**:
- Vulnerabilities in third-party dependencies with no exploitable path in this project — report those
  upstream. If the path *is* exploitable here, it is in scope; say so.
- The behaviour of the LLM itself (model output quality, refusals, hallucination). Prompt injection
  that **crosses a security boundary** — escaping the read-only enforcement, reaching a tool outside
  the allowlist, exfiltrating a credential — **is** in scope and is exactly what we want to hear
  about.
- Findings from an automated scanner pasted without an exploitability argument.
- Denial of service via unbounded self-inflicted load (this is a self-hosted platform; you control
  its resources).

## Safe harbour

If you make a good-faith effort to follow this policy, we will not pursue or support any action
against you for your research. Please avoid privacy violations, data destruction, and any disruption
of systems you do not own — and test against your own deployment, never against someone else's.

## Recognition

We will credit you in the release notes and the security advisory unless you ask us not to.
