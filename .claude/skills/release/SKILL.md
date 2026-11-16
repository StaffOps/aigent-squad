---
name: release
description: Cut an aigent-squad release (dev → main → tag → chart → cluster). Use when asked to release, tag, or publish a version.
---

# Release

The canonical, ordered checklist is **`RELEASE.md`** (spec 34) — follow it
verbatim; do not improvise steps from memory.

Rules (branch flow, never re-tag, when a version bump is justified) live in
`RELEASE.md` and `specs/ROADMAP.md` → "Versioning principles"; do not restate
them here. If a step in `RELEASE.md` does not match reality, STOP and ask the
human rather than improvising.
