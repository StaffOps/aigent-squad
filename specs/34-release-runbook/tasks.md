# Tasks: Release Runbook

> Depends on spec 32 (status gate as pre-flight) and spec 33 (review as a close step).
> Verification pipeline per `specs/README.md`.

- [ ] T1: Write `RELEASE.md` — phases 0–7 per design; every step a command or a single
      verifiable check; cross-repo steps labeled `[helm-charts]` / `[k8s-setup]`
- [ ] T2: Embed the version-bump rule (version-management) + the "current bump gate"
      pointer to ROADMAP (today: 0.4.0 ⇐ spec-14 findings A/B/D closed) (depends on: T1)
- [ ] T3: Write the homologation smoke list (health · per-agent real query · injection
      probe → 403 · admission headers · MCP round-trip), reusing the actual probes from
      the 2026-07-01/03 homologation sessions (depends on: T1)
- [ ] T4: Dry-run validation — map every ad-hoc action from the 0.2.0 and 0.3.0 cycles
      (HANDOFF/archive records) onto a runbook step; zero orphan actions; gaps become
      steps or BACKLOG items (depends on: T1–T3)
- [ ] T5: Cross-references — AGENTS.md + specs/README.md lifecycle point at RELEASE.md;
      HANDOFF "next steps" convention references it (depends on: T1)
- [ ] T6: Independent review — sequencing correct vs real CI behavior (release.yml,
      guard, chart-releaser, docs.yml-from-main), coherence rule unambiguous, credential
      step present (depends on: T4, T5)
- [ ] T7 (deferred until it happens): execute RELEASE.md verbatim on the 0.4.0 release;
      fold friction back into the runbook in the same PR

## Order
T1 → T2/T3 → T4 → T5 → T6; T7 rides the next real release.

## Notes
- The runbook sequences EXISTING CI — any CI gap found in T4 is a BACKLOG item, not scope.
- Immutable tags: a bad release gets X.Y.Z+1, never a re-tag.
- Credential hygiene (revoke cycle-temporary tokens) is a standing close step — never a
  handoff TODO again.
