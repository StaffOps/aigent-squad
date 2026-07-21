#!/usr/bin/env python3
"""Eval harness: run golden queries against the live gateway and score behavior.

Reads evals/golden_queries.yaml (or $GOLDEN_FILE, or inline $GOLDEN_JSON), sends
each case to $AIGENT_GATEWAY (default http://localhost:8000) with a Bearer token
from $AIGENT_TOKEN / $INTERNAL_API_TOKEN / $AIGENT_SQUAD_API_KEY, and scores the
(streamed) response against per-case assertions:
  - expect_status: HTTP status (200 for allowed, 403 for guardrail-blocked)
  - must_match:     list of regex (all must match, case-insensitive)
  - must_not_contain: list of substrings (none may appear)

Exit non-zero if any case fails — usable as a CI accuracy gate. Run inside the
gateway pod (localhost) or via port-forward.
"""
from __future__ import annotations
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

GATEWAY = os.environ.get("AIGENT_GATEWAY", "http://localhost:8000")
TOKEN = (os.environ.get("AIGENT_TOKEN") or os.environ.get("INTERNAL_API_TOKEN")
         or os.environ.get("AIGENT_SQUAD_API_KEY", ""))
TIMEOUT = int(os.environ.get("EVAL_TIMEOUT", "150"))


def _load_cases() -> list[dict]:
    if os.environ.get("GOLDEN_JSON"):
        return json.loads(os.environ["GOLDEN_JSON"])
    path = Path(os.environ.get("GOLDEN_FILE",
                str(Path(__file__).resolve().parents[1] / "evals" / "golden_queries.yaml")))
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def run_case(c: dict) -> tuple[list[str], int, int]:
    body = json.dumps({"model": c["model"], "stream": True,
                       "messages": [{"role": "user", "content": c["query"]}]}).encode()
    req = urllib.request.Request(
        f"{GATEWAY}/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    status, buf = 200, []
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            for raw in r:
                line = raw.decode(errors="replace").strip()
                if line.startswith("data:") and line != "data: [DONE]":
                    try:
                        buf.append(json.loads(line[5:])["choices"][0]["delta"].get("content") or "")
                    except Exception:
                        pass
    except urllib.error.HTTPError as e:
        status = e.code
    text = "".join(buf)
    fails: list[str] = []
    if "expect_status" in c and status != c["expect_status"]:
        fails.append(f"status {status} != {c['expect_status']}")
    for pat in c.get("must_match", []):
        if not re.search(pat, text, re.I):
            fails.append(f"missing /{pat}/")
    for s in c.get("must_not_contain", []):
        if s.lower() in text.lower():
            fails.append(f"present '{s}'")
    return fails, status, len(text)


def main() -> int:
    cases = _load_cases()
    passed = 0
    for c in cases:
        fails, status, n = run_case(c)
        ok = not fails
        passed += ok
        tag = "PASS" if ok else "FAIL"
        extra = "" if ok else "  -> " + "; ".join(fails)
        print(f"  [{tag}] {c['name']} (status={status}, {n} chars){extra}")
    total = len(cases)
    print(f"\nSCORE: {passed}/{total}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
