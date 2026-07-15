"""Spec 35 Phase 3 (T8/T9) — RCA scenario eval runner (`make eval-rca`).

Fixture-fed scenarios (design.md Decision 3): each scenario in evals/rca/*.yaml
supplies canned "adapter output" per agent. The investigation pipeline itself
(fan-out evidence collection -> timeline/correlate -> RCA synthesis) runs for
REAL against Bedrock via run_investigation() — only the *world* (what the
adapters would have returned) is fixed. This makes the eval both a quality
score AND, later, the acceptance test for spec 18 Phase 1.5 (EVIDENCE-MODEL
correlator): re-run after that ships to measure the gain over this baseline.

Score per scenario:
- mechanical: hypothesis matches expected_keywords (regex) AND confidence is
  >= expected_confidence_min (baixa < media < alta). A mechanical failure
  zeroes the scenario, same anchor-first design as the T2 golden-set runner.
- no LLM judge here (unlike T2): the hypothesis text and confidence level are
  a strong enough signal for a 3-scenario baseline; a judge pass is a Phase-3
  follow-up if the mechanical score alone proves too coarse in practice.

Never runs implicitly — only via explicit `make eval-rca`. Real Bedrock cost:
per scenario, up to 6 agent fan-out calls (Sonnet) + 1 synthesis call (Sonnet)
-- budget ~$0.50-1.50 for all 3 scenarios.

Usage: python evals/rca_runner.py (run inside Docker via
scripts/eval-rca-local.sh, real AWS creds mounted, same conventions as
scripts/eval-local.sh)
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, "/app")

from src.core.adapters import DatasourceAdapter  # noqa: E402
from src.core.generic_agent import GenericAgent  # noqa: E402
from src.core.metrics import eval_score  # noqa: E402
from src.core.registry import AgentRegistry  # noqa: E402
from src.core.skills import SkillRegistry  # noqa: E402
from src.supervisor.investigation import run_investigation  # noqa: E402

EVALS_DIR = Path(__file__).parent
RCA_DIR = EVALS_DIR / "rca"
RESULTS_DIR = EVALS_DIR / "results"

CONFIDENCE_RANK = {"baixa": 1, "media": 2, "alta": 3}
DEFAULT_FIXTURE = "No relevant signal collected for this investigation."


class FixtureAdapter(DatasourceAdapter):
    """Returns a fixed canned text regardless of query — the "world" the
    scenario YAML describes. No caching (each scenario run should be fresh)."""

    cache_ttl = 0

    def __init__(self, fixture_text: str):
        self._text = fixture_text

    async def _collect(self, query: str) -> str:
        return self._text


def _load_scenarios() -> list[dict]:
    return [yaml.safe_load(p.read_text()) for p in sorted(RCA_DIR.glob("*.yaml"))]


def _build_fixture_agents(scenario: dict, registry: AgentRegistry, skill_registry: SkillRegistry) -> dict:
    fixtures = scenario.get("fixtures", {})
    agents = {}
    for config in registry.list_agents():
        text = fixtures.get(config.name, DEFAULT_FIXTURE)
        prompt = registry.get_prompt(config.name)
        agents[config.name] = GenericAgent(config, prompt, [FixtureAdapter(text)], skill_registry=skill_registry)
    return agents


def _score(scenario: dict, rca) -> dict:
    hypothesis = rca.hypothesis or ""
    keyword_patterns = scenario.get("expected_keywords", [])
    keyword_hit = any(re.search(p, hypothesis) for p in keyword_patterns) if keyword_patterns else True

    min_conf = scenario.get("expected_confidence_min", "baixa")
    confidence_ok = CONFIDENCE_RANK.get(rca.confidence, 0) >= CONFIDENCE_RANK.get(min_conf, 0)

    passed = keyword_hit and confidence_ok
    failures = []
    if not keyword_hit:
        failures.append(f"hypothesis did not match any expected_keywords: {keyword_patterns}")
    if not confidence_ok:
        failures.append(f"confidence '{rca.confidence}' below expected_confidence_min '{min_conf}'")

    return {
        "id": scenario["id"],
        "score": 1.0 if passed else 0.0,
        "passed": passed,
        "confidence": rca.confidence,
        "hypothesis": hypothesis,
        "evidence_count": len(rca.evidence),
        "failures": failures,
    }


async def run() -> dict:
    registry = AgentRegistry()
    registry.discover()
    skill_registry = SkillRegistry()
    skill_registry.discover()

    scenarios = _load_scenarios()
    results = []
    for scenario in scenarios:
        agents = _build_fixture_agents(scenario, registry, skill_registry)
        rca = await run_investigation(
            symptom=scenario["symptom"],
            agents=agents,
            user_id="rca-eval",
            session_id=f"rca-eval-{scenario['id']}",
        )
        result = _score(scenario, rca)
        eval_score.record(result["score"], {"suite": "rca", "agent_id": result["id"]})
        print(f"[{result['id']}] score={result['score']} confidence={result['confidence']}")
        if not result["passed"]:
            print(f"  hypothesis: {result['hypothesis'][:200]}")
            for f in result["failures"]:
                print(f"  FAIL: {f}")
        results.append(result)

    avg = sum(r["score"] for r in results) / len(results) if results else 0.0
    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "avg_score": avg,
        "scenarios": results,
    }
    return summary


def main() -> None:
    import asyncio

    summary = asyncio.run(run())

    RESULTS_DIR.mkdir(exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = RESULTS_DIR / f"rca-{date_str}.json"
    out_path.write_text(json.dumps(summary, indent=2))

    print(f"\navg_score={summary['avg_score']:.2f} ({len(summary['scenarios'])} scenarios)")
    print(f"Results written to {out_path}")

    baseline_path = RESULTS_DIR / "rca-baseline.json"
    if not baseline_path.exists():
        baseline_path.write_text(json.dumps(summary, indent=2))
        print(f"No RCA baseline existed — this run IS the baseline ({baseline_path}).")


if __name__ == "__main__":
    main()
