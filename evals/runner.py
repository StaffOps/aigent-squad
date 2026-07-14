"""Spec 35 T2 — golden-set eval runner (`make eval`).

Executes each agent's golden set against the REAL local stack (gateway
/query — same entrypoint smoke.sh uses), scores mechanical checks
(must_contain_regex / must_not_contain_regex / routing_expected) first, then
an LLM judge (Haiku, temperature 0, versioned rubric in judge_prompt.md) for
the coherence/actionability residue mechanical checks can't cover.

Per-question score: mechanical checks are the anchor (design.md Decision 2)
— a mechanical failure zeroes the question's score regardless of judge
opinion; only when ALL mechanical checks pass does the judge's
(coherence + actionability) / 10 score apply.

Never runs implicitly — only via explicit `make eval` (design.md Invariant).
Real Bedrock cost (the judge calls) + real gateway calls (whatever the
agent's own real invoke costs) — budget ~$1-3/run per spec 35 requirements.

Usage: python evals/runner.py  (run inside Docker via scripts/eval-local.sh,
network-joined to the local stack, real AWS creds mounted — see that script)
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

sys.path.insert(0, "/app")

from src.core.bedrock import bedrock  # noqa: E402
from src.core.metrics import eval_score  # noqa: E402

EVALS_DIR = Path(__file__).parent
GOLDEN_DIR = EVALS_DIR / "golden"
RESULTS_DIR = EVALS_DIR / "results"
JUDGE_PROMPT = (EVALS_DIR / "judge_prompt.md").read_text()

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://gateway:8000")
INTERNAL_API_TOKEN = os.environ.get("INTERNAL_API_TOKEN", "dev-secret-token")

# Tolerance band: a per-agent score drop beyond this vs baseline is flagged
# (spec 34 would treat it as a release blocker — informational only here,
# nothing in this repo enforces spec 34 yet).
TOLERANCE = 0.15


async def _ask(question: str, session_id: str) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f"{GATEWAY_URL}/query",
            headers={"X-Internal-Token": INTERNAL_API_TOKEN},
            json={"user_input": question, "user_id": "eval", "session_id": session_id},
        )
        r.raise_for_status()
        return r.json()


def _mechanical_checks(response_text: str, actual_agent: str, case: dict) -> tuple[bool, list[str]]:
    """Returns (all_passed, failure_reasons)."""
    failures: list[str] = []

    for pattern in case.get("must_contain_regex", []) or []:
        if not re.search(pattern, response_text):
            failures.append(f"missing required pattern: {pattern!r}")

    for pattern in case.get("must_not_contain_regex", []) or []:
        if re.search(pattern, response_text):
            failures.append(f"forbidden pattern present: {pattern!r}")

    expected_agent = case.get("routing_expected")
    if expected_agent and actual_agent.lower() != expected_agent.lower():
        failures.append(f"routing: expected {expected_agent!r}, got {actual_agent!r}")

    return (len(failures) == 0, failures)


async def _judge(question: str, answer: str) -> dict:
    """Score coherence+actionability via Haiku (temp 0, versioned rubric)."""
    user_msg = f"Question: {question}\n\nAnswer: {answer}"
    raw = await bedrock.invoke(
        messages=[{"role": "user", "content": user_msg}],
        system_prompt=JUDGE_PROMPT,
        temperature=0.0,
        match_user_language=False,
        agent_id="eval-judge",
        user_id="eval",
        session_id="eval-judge",
        role="classifier",  # Haiku tier (spec 11) — matches design.md's cost intent
    )
    try:
        start, end = raw.find("{"), raw.rfind("}")
        parsed = json.loads(raw[start:end + 1])
        return {
            "coherence": int(parsed.get("coherence", 0)),
            "actionability": int(parsed.get("actionability", 0)),
            "note": parsed.get("note", ""),
        }
    except (json.JSONDecodeError, ValueError):
        return {"coherence": 0, "actionability": 0, "note": "judge output unparsable"}


async def run() -> dict:
    all_results: dict[str, list[dict]] = {}

    for golden_file in sorted(GOLDEN_DIR.glob("*.yaml")):
        spec = yaml.safe_load(golden_file.read_text())
        agent_name = spec["agent"]
        agent_results = []

        for case in spec["questions"]:
            session_id = f"eval-{agent_name}-{case['id']}"
            try:
                resp = await _ask(case["question"], session_id)
            except Exception as e:
                agent_results.append({
                    "id": case["id"], "question": case["question"],
                    "mechanical_pass": False, "failures": [f"request error: {e}"],
                    "judge": None, "score": 0.0,
                })
                continue

            response_text = resp.get("response", "")
            actual_agent = resp.get("agent", "")
            mech_pass, failures = _mechanical_checks(response_text, actual_agent, case)

            judge = None
            score = 0.0
            if mech_pass:
                judge = await _judge(case["question"], response_text)
                score = (judge["coherence"] + judge["actionability"]) / 10.0

            agent_results.append({
                "id": case["id"], "question": case["question"],
                "mechanical_pass": mech_pass, "failures": failures,
                "judge": judge, "score": round(score, 3),
            })
            eval_score.record(score, {"suite": "golden", "agent_id": agent_name})

        all_results[agent_name] = agent_results

    return all_results


def _summarize(all_results: dict) -> dict:
    summary = {}
    for agent_name, cases in all_results.items():
        scores = [c["score"] for c in cases]
        summary[agent_name] = {
            "avg_score": round(sum(scores) / len(scores), 3) if scores else 0.0,
            "questions": len(cases),
            "mechanical_failures": sum(1 for c in cases if not c["mechanical_pass"]),
        }
    return summary


def main() -> None:
    import asyncio

    all_results = asyncio.run(run())
    summary = _summarize(all_results)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    result_doc = {
        "date": date_str,
        "judge_prompt_version": "v1 (2026-07-14)",
        "summary": summary,
        "results": all_results,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result_path = RESULTS_DIR / f"{date_str}.json"
    result_path.write_text(json.dumps(result_doc, indent=2))
    print(f"Results written to {result_path}")

    baseline_path = RESULTS_DIR / "baseline.json"
    if not baseline_path.exists():
        baseline_path.write_text(json.dumps(result_doc, indent=2))
        print(f"No baseline existed — recorded this run as the baseline ({baseline_path})")
    else:
        baseline = json.loads(baseline_path.read_text())
        print("\n--- Diff vs baseline ---")
        regressions = []
        for agent_name, agent_summary in summary.items():
            base_score = baseline.get("summary", {}).get(agent_name, {}).get("avg_score")
            cur_score = agent_summary["avg_score"]
            if base_score is None:
                print(f"  {agent_name}: {cur_score} (no baseline entry)")
                continue
            delta = cur_score - base_score
            flag = " *** REGRESSION ***" if delta < -TOLERANCE else ""
            if flag:
                regressions.append(agent_name)
            print(f"  {agent_name}: {base_score} -> {cur_score} ({delta:+.3f}){flag}")
        if regressions:
            print(f"\nRegression beyond tolerance ({TOLERANCE}) in: {', '.join(regressions)}")

    print("\n--- Summary ---")
    for agent_name, s in summary.items():
        print(f"  {agent_name}: avg_score={s['avg_score']} "
              f"({s['questions']} questions, {s['mechanical_failures']} mechanical failures)")


if __name__ == "__main__":
    main()
