---
spec: 04-harden-security
status: done
completed: null
superseded_by: null
depends_on: []
deferred: []
---

# Feature: Harden Security

**Spec**: `04-harden-security`
**Severity**: 🟠 High
**Findings**: S1, S2, S3, S4, S5 (see `../AUDIT.md`)

Apply security by default to the services, aligned with `cloud-security.md`, `k8s-best-practices.md` and `12-factor-app.md`. Today the endpoints are open, containers run as root, and Redis has no auth.

## User Stories

WHEN a client calls any `/process` or `/query` endpoint THEN the service SHALL require authentication (shared token in dev; mTLS/NetworkPolicy in prod).

WHEN a squad container starts THEN it SHALL run as a non-root user with read-only filesystem and dropped capabilities.

WHEN the system runs in production THEN AWS access SHALL use IRSA (not mounted credentials), and Redis SHALL have auth + TLS.

WHEN untrusted data (user input, GitLab/docs/inventory outputs) enters the prompt THEN they SHALL be delimited to reduce prompt injection.

## Acceptance Criteria

- [ ] Internal endpoints require an auth header (`X-Internal-Token`) validated by shared env; absence → 401.
- [ ] MCP server and supervisor validate the token before routing.
- [ ] Dockerfiles define a non-root `USER`; manifests/compose define `securityContext` (runAsNonRoot, readOnlyRootFilesystem, drop ALL) where applicable.
- [ ] `docker-compose` documents/defines Redis with password; `REDIS_SSL` configurable.
- [ ] Documented in `docs/` that prod uses IRSA + External Secrets (without `~/.aws` mounted).
- [ ] Prompts place untrusted data inside clear delimiters (e.g.: `<untrusted_data>` blocks).
- [ ] Example NetworkPolicy (or note in design) restricting who calls the agents.

## Out of scope

- Implement Istio Ambient/mTLS in the cluster (depends on external infra) — document as prod target, deliver shared token for dev/local.
- Kyverno policies (reference to steering; not implemented here).
