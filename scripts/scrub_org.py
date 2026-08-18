#!/usr/bin/env python3
"""Scrub all company (BigDataCorp/BDC) references from the aigent-squad project.

Replaces org identifiers with neutral placeholders and renames the 6 bdc-named
skill directories (updating every reference, incl. agent.yaml `skills:` lists).
Builds/CI carry NO org strings (verified), so this is docs/skills/prose only —
no functional breakage.
"""
from __future__ import annotations
import os
import re
import sys
from pathlib import Path

ROOT = Path(os.environ.get("SCRUB_ROOT", str(Path(__file__).resolve().parents[1])))
SKILLS = ROOT / "skills"

# skill dir renames (old -> new). Applied to dirs AND as text replacements.
SKILL_RENAMES = {
    "bdc-telemetry-standard": "telemetry-standard",
    "bdc-telemetry-helper": "telemetry-helper",
    "helm-chart-app-bdc": "helm-chart-app",
    "helm-chart-cronworkflow-bdc": "helm-chart-cronworkflow",
    "kyverno-bdc-policies": "kyverno-policies",
    "terraform-modules-bdc": "terraform-modules",
}

# ordered (specific first). Case-sensitive regex on the literal.
REPLACEMENTS = [
    (r"harbor\.bigdatacorp\.com\.br", "harbor.<org>.com"),
    (r"bigdatacorp\.com\.br", "<org>.com"),
    (r"bigdatacorp\.info", "<org>.info"),
    (r"gitlab\.com/BigDataCorp", "gitlab.com/<ORG>"),
    (r"BigDataCorp", "<ORG>"),
    (r"bigdatacorp", "<org>"),
    # domains / infra identifiers
    (r"\.bdc\.app\.br", ".<org>.app.br"),
    (r"\bbdc\.app\.br", "<org>.app.br"),
    (r"\bbdc\.api\.br", "<org>.api.br"),
    (r"\bbdc\.internal", "<org>.internal"),
    (r"https-bdc-app-br", "https-<org>-app-br"),
    (r"bdc-images", "<org>-images"),
    (r"bdc-workloads", "<org>-workloads"),
    # bare/compound forms LAST (substring, case-mapped; skill renames already done).
    # Catches compounds like BDCOtelHelper, bdc_otel, bdctelemetryhelper, BdcEnvironment.
    (r"BDC", "<ORG>"),
    (r"Bdc", "<ORG>"),
    (r"bdc", "<org>"),
]

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}
SKIP_EXT = {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".gz", ".zip"}


def apply_replacements(text: str) -> str:
    for old, new in SKILL_RENAMES.items():
        text = text.replace(old, new)
    for pat, new in REPLACEMENTS:
        text = re.sub(pat, new, text)
    return text


def main() -> int:
    # 1. rename skill dirs
    for old, new in SKILL_RENAMES.items():
        src, dst = SKILLS / old, SKILLS / new
        if src.exists() and not dst.exists():
            os.rename(src, dst)
            print(f"  renamed skill: {old} -> {new}")
    # 2. scrub all text files
    changed = 0
    self_path = Path(__file__).resolve()
    for f in ROOT.rglob("*"):
        if not f.is_file():
            continue
        if f.resolve() == self_path:
            continue
        if any(p in SKIP_DIRS for p in f.parts) or f.suffix in SKIP_EXT:
            continue
        try:
            orig = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new = apply_replacements(orig)
        if new != orig:
            f.write_text(new, encoding="utf-8")
            changed += 1
    print(f"CHANGED {changed} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
