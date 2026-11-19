"""T11 formal smoke — specs 06, 17 and 18.

These close the "formal smoke" task the three specs deferred (manual smoke only).
Unlike the unit tests in ``test_fanout.py`` / ``test_run_investigation.py``, which
mock ``GenericAgent.process_request`` directly, every test here drives the REAL
request path end to end in-process:

    POST /internal/process  (real FastAPI app, real internal-token auth,
                             real SessionLock on fakeredis)
      → SupervisorAgent.process_request  (real triage, InputScanner, classifier)
        → GenericAgent.process_request   (real agents/ configs + prompts, real
                                          legacy path, OutputFilter, canary)
          → BedrockClient.invoke         (LLMService.invoke since spec 28: real
                                          semaphore, circuit breaker and
                                          asyncio.to_thread offload)
            → BedrockClient._invoke_sync     ← the ONLY stubbed LLM seam (patched on
                                          the live BedrockClient instance)

Stubbed: ``_invoke_sync`` (no AWS), datasource adapters (canned text, no boto3 /
MCP), DynamoDB history (in-memory), KB RAG lookup and KB distillation (both
would issue extra Bedrock embedding/extraction calls that are out of scope).
No compose stack, no host ports.

The Bedrock stub uses a *blocking* ``time.sleep`` for its artificial latency on
purpose: it runs inside the real ``asyncio.to_thread`` offload, so the spec-06
parallelism assertion fails if the async path ever regresses to a blocking call
(the exact failure mode spec 06 exists to prevent).
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import fakeredis.aioredis
import httpx
import pytest
import pytest_asyncio

from src.core.adapters import DatasourceAdapter
from src.core.circuit_breaker import CircuitBreaker
from src.core.config import settings
from src.core.registry import AgentRegistry

AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"
INTERNAL_TOKEN = "t11-smoke-internal-token"
AGENT_DELAY_S = 0.4  # per-agent artificial Bedrock latency

EVIDENCE_QUERY_MARKER = "Collect evidence for the following symptom"

# Per-agent evidence for the RCA path: distinct signal types and causal-looking
# timestamps so the correlator/timeline have real work to do.
_EVIDENCE_BY_AGENT = {
    "aws": ("infra", "2026-11-15T10:02:00Z", "media", "RDS connections near max"),
    "devops": ("deploy", "2026-11-15T10:00:00Z", "forte", "checkout v2.3.1 deployed"),
    "finops": ("metric", "2026-11-15T10:05:00Z", "fraca", "no cost anomaly"),
    "kubernetes": ("event", "2026-11-15T10:01:00Z", "forte", "checkout pods restarted"),
    "observability": ("metric", "2026-11-15T10:01:30Z", "forte", "p99 latency 4s"),
    "security": ("log", "2026-11-15T10:03:00Z", "fraca", "no security findings"),
}

_RCA_JSON = json.dumps({
    "hypothesis": "checkout v2.3.1 deploy exhausted the RDS connection pool",
    "reasoning": "deploy precedes restarts and latency spike",
    "contradicting_evidence_indices": [],
    "prevention": ["alert on RDS connection saturation", "canary the checkout deploy"],
})


# ── Fakes ────────────────────────────────────────────────────────────────────


class _CannedAdapter(DatasourceAdapter):
    """Datasource stub: returns fixed text, never touches boto3/MCP/HTTP."""

    cache_ttl = 0  # bypass the Redis datasource cache entirely

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name

    async def _collect(self, query: str) -> str:
        return f"[{self.agent_name}] canned datasource snapshot"


class _InMemoryStorage:
    """Async stand-in for the DynamoDB ChatStorage (same method surface)."""

    def __init__(self) -> None:
        self.saved: list[tuple[str, str, str, Any]] = []

    async def save_chat_message(self, user_id, session_id, agent_id, message, max_history=20):
        self.saved.append((user_id, session_id, agent_id, message))

    async def fetch_chat(self, user_id, session_id, agent_id, max_messages=20):
        return [m for (u, s, a, m) in self.saved if (u, s, a) == (user_id, session_id, agent_id)]

    async def fetch_all_chats(self, user_id, session_id, max_messages=50):
        return [m for (u, s, _a, m) in self.saved if (u, s) == (user_id, session_id)]


@dataclass
class _Call:
    role: str
    agent_id: str
    session_id: str
    budget_session_id: str | None
    system_prompt: str
    user_text: str
    start: float
    end: float = 0.0


@dataclass
class _FakeBedrock:
    """Replacement for ``BedrockClient._invoke_sync`` (same positional signature).

    Dispatches on the logical role/agent_id the real callers pass, so the test
    sees exactly which Bedrock calls the orchestration made.
    """

    classifier_reply: dict[str, Any] = field(default_factory=dict)
    agent_delay: float = AGENT_DELAY_S
    calls: list[_Call] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __call__(
        self, messages, system_prompt, max_tokens=4096, temperature=0.7,
        use_cache=True, agent_id="unknown", match_user_language=True,
        user_id="unknown", session_id="", role="agent",
        budget_session_id=None, skip_input_guardrail=False,
    ) -> str:
        user_text = messages[-1]["content"]
        call = _Call(role, agent_id, session_id, budget_session_id,
                     system_prompt, user_text, start=time.perf_counter())
        with self._lock:
            self.calls.append(call)
        try:
            return self._reply(call)
        finally:
            call.end = time.perf_counter()

    def _reply(self, call: _Call) -> str:
        if call.role == "classifier":
            return json.dumps(self.classifier_reply)
        if call.role == "synthesis" and call.agent_id == "synthesizer":
            return "SYNTHESIZED: merged answer from " + ", ".join(
                name for name in _EVIDENCE_BY_AGENT if f'<agent name="{name}">' in call.user_text
            )
        if call.role == "synthesis" and call.agent_id == "rca-synthesizer":
            return _RCA_JSON
        if call.role == "agent":
            time.sleep(self.agent_delay)  # blocking on purpose — see module docstring
            if EVIDENCE_QUERY_MARKER in call.user_text:
                sig, ts, strength, summary = _EVIDENCE_BY_AGENT[call.agent_id]
                return json.dumps([{
                    "signal_type": sig, "timestamp": ts,
                    "strength": strength, "summary": summary,
                }])
            return f"{call.agent_id} answer: all good"
        raise AssertionError(f"unexpected Bedrock call role={call.role} agent={call.agent_id}")

    # Convenience views
    def by_role(self, role: str, agent_id: str | None = None) -> list[_Call]:
        return [c for c in self.calls if c.role == role and (agent_id is None or c.agent_id == agent_id)]


# ── Harness ──────────────────────────────────────────────────────────────────


@dataclass
class _Harness:
    client: httpx.AsyncClient
    bedrock: _FakeBedrock
    storage: _InMemoryStorage
    supervisor: Any

    async def process(self, user_input: str, mode: str | None = None,
                      session_id: str = "s-t11") -> tuple[httpx.Response, float]:
        body: dict[str, Any] = {"user_input": user_input, "user_id": "u-t11", "session_id": session_id}
        if mode:
            body["mode"] = mode
        t0 = time.perf_counter()
        resp = await self.client.post(
            "/internal/process", json=body, headers={"X-Supervisor-Token": INTERNAL_TOKEN},
        )
        return resp, time.perf_counter() - t0


@pytest_asyncio.fixture
async def harness(monkeypatch):
    import src.core.bedrock as bedrock_mod
    import src.core.classifier as classifier_mod
    import src.core.generic_agent as generic_agent_mod
    import src.supervisor.agent as agent_mod
    import src.supervisor.investigation as investigation_mod
    import src.supervisor.server as srv
    import src.supervisor.synthesizer as synthesizer_mod

    monkeypatch.setenv("AWS_REGION", "us-east-1")  # required_env of aws/finops/security
    monkeypatch.setattr(settings, "supervisor_internal_token", INTERNAL_TOKEN)
    monkeypatch.setattr(settings, "aigent_tier_routing_enabled", False)
    monkeypatch.setattr(investigation_mod, "MAX_AGENTS_PER_INVESTIGATION", 5)

    # Bedrock: stub ONLY the sync chokepoint. Other test files reload some of
    # these modules, so patch every live BedrockClient instance the request path
    # can reach (normally they are one and the same object).
    fake = _FakeBedrock()
    live = [bedrock_mod.bedrock] + [m.llm for m in (
        classifier_mod, generic_agent_mod, synthesizer_mod, investigation_mod,
    )]
    clients = {id(c): c for c in live}
    for client in clients.values():
        monkeypatch.setattr(client, "_invoke_sync", fake)
        monkeypatch.setattr(client, "circuit_breaker", CircuitBreaker("bedrock-t11"))
        monkeypatch.setattr(client, "_semaphore", None)  # fresh, bound to this loop

        def _no_converse(*_a, **_kw):
            raise AssertionError("agentic converse path must not run in the T11 smoke")
        monkeypatch.setattr(client, "_converse_sync", _no_converse)

    storage = _InMemoryStorage()
    monkeypatch.setattr(agent_mod, "storage", storage)
    monkeypatch.setattr(agent_mod, "distill_rca", AsyncMock())
    monkeypatch.setattr(investigation_mod, "inject_similar_cases", AsyncMock(return_value=""))
    monkeypatch.setattr(
        agent_mod, "create_adapters",
        lambda configs, cache_ttl=0, cache_namespace="default": [_CannedAdapter(cache_namespace)],
    )

    registry = AgentRegistry(agents_dir=str(AGENTS_DIR))
    registry.discover()
    supervisor = agent_mod.SupervisorAgent(registry)
    monkeypatch.setattr(srv, "supervisor", supervisor)
    monkeypatch.setattr(srv, "_session_redis", fakeredis.aioredis.FakeRedis(decode_responses=True))

    # Dedicated default executor: asyncio.to_thread uses the loop's default
    # executor, whose size (min(32, cpu+4)) varies per runner — a small CI box
    # would serialize the parallel fan-out and make the timing assertions flaky.
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="t11-smoke")
    loop.set_default_executor(executor)

    transport = httpx.ASGITransport(app=srv.app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://supervisor") as client:
            yield _Harness(client=client, bedrock=fake, storage=storage, supervisor=supervisor)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _assert_all_overlapped(calls: list[_Call]) -> None:
    """Every call started before ANY call finished → they were all in flight at once."""
    assert max(c.start for c in calls) < min(c.end for c in calls), (
        "agent Bedrock calls did not overlap — fan-out ran (partly) sequentially"
    )


def _assert_message_contract(storage: _InMemoryStorage, session_id: str) -> None:
    """Invariant 9: every persisted message is {role, content, timestamp, agent_id}."""
    msgs = [m for (_u, s, _a, m) in storage.saved if s == session_id]
    assert msgs, "nothing was persisted to history"
    for m in msgs:
        assert m.role in ("user", "assistant")
        assert isinstance(m.content, str) and m.content
        assert isinstance(m.timestamp, str) and m.timestamp
        assert isinstance(m.agent_id, str) and m.agent_id


# ── Spec 06 T11 ──────────────────────────────────────────────────────────────


class TestSpec06T11ParallelRcaSmoke:
    """Spec 06 T11: mocked RCA with 5 agents completes in ~1× wall time, not 5×."""

    @pytest.mark.asyncio
    async def test_five_agent_rca_runs_in_parallel(self, harness):
        resp, elapsed = await harness.process(
            "checkout latency spiked, investigate", mode="investigate", session_id="s-06",
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["agent"] == "investigation"

        agent_calls = harness.bedrock.by_role("agent")
        assert len(agent_calls) == 5, "RCA cost cap: exactly 5 agents consulted"
        assert len({c.agent_id for c in agent_calls}) == 5

        # Deterministic parallelism proof (no wall-clock dependence)...
        _assert_all_overlapped(agent_calls)
        # ...plus the wall-clock budget: ~1× the per-agent delay, far below 5×.
        # 4× leaves generous headroom for slow CI; sequential would be ≥5×.
        assert elapsed < AGENT_DELAY_S * 4, f"RCA took {elapsed:.2f}s (sequential ≈ {AGENT_DELAY_S * 5:.1f}s)"


# ── Spec 17 T11 ──────────────────────────────────────────────────────────────


class TestSpec17T11FanOutSmoke:
    """Spec 17 T11: single-domain fast path (no synthesis) + cross-domain fan-out + synthesis."""

    @pytest.mark.asyncio
    async def test_single_domain_query_takes_fast_path_without_synthesis(self, harness):
        harness.bedrock.classifier_reply = {
            "agents": [{"agent": "aws", "confidence": 0.95, "sub_query": "Count running EC2 instances."}],
            "reasoning": "single-domain AWS inventory",
            "complexity": "simple",
        }

        resp, _ = await harness.process("How many EC2 instances are running?", session_id="s-17a")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["agent"] == "aws"
        assert body["response"] == "aws answer: all good"
        assert body["confidence"] == 0.95
        assert "agents_consulted" not in body  # not the fan-out shape

        b = harness.bedrock
        assert len(b.by_role("classifier")) == 1
        assert [c.agent_id for c in b.by_role("agent")] == ["aws"]
        assert b.by_role("synthesis") == [], "fast path must not pay for a synthesis call"
        # B-14: the agent received the classifier's focused sub_query.
        assert "Count running EC2 instances." in b.by_role("agent")[0].user_text

        _assert_message_contract(harness.storage, "s-17a")
        saved_roles = [(a, m.role) for (_u, s, a, m) in harness.storage.saved if s == "s-17a"]
        assert saved_roles == [("aws", "user"), ("aws", "assistant")]

    @pytest.mark.asyncio
    async def test_cross_domain_query_fans_out_and_synthesizes(self, harness):
        harness.bedrock.classifier_reply = {
            "agents": [
                {"agent": "finops", "confidence": 0.9, "sub_query": "EC2 spend this month."},
                {"agent": "aws", "confidence": 0.85, "sub_query": "EC2 instance inventory."},
                {"agent": "kubernetes", "confidence": 0.8, "sub_query": "Node count this month."},
            ],
            "reasoning": "cost + inventory + cluster",
            "complexity": "complex",
        }

        resp, elapsed = await harness.process(
            "Compare EC2 spend with the Kubernetes node count for this month", session_id="s-17b",
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["agent"] == "supervisor"
        assert body["agents_consulted"] == ["finops", "aws", "kubernetes"]
        assert body["agents_failed"] == []
        assert body["confidence"] == 0.8  # min across consulted agents
        assert body["response"].startswith("SYNTHESIZED:")

        b = harness.bedrock
        assert len(b.by_role("classifier")) == 1
        agent_calls = b.by_role("agent")
        assert sorted(c.agent_id for c in agent_calls) == ["aws", "finops", "kubernetes"]
        _assert_all_overlapped(agent_calls)
        assert elapsed < AGENT_DELAY_S * 4

        synth = b.by_role("synthesis")
        assert len(synth) == 1 and synth[0].agent_id == "synthesizer"
        # The single synthesis call saw all three attributed agent answers.
        for name in ("finops", "aws", "kubernetes"):
            assert f'<agent name="{name}">\n{name} answer: all good' in synth[0].user_text
        assert body["response"] == "SYNTHESIZED: merged answer from aws, finops, kubernetes"

        _assert_message_contract(harness.storage, "s-17b")
        saved = [(a, m.role, m.content) for (_u, s, a, m) in harness.storage.saved if s == "s-17b"]
        assert saved[0][:2] == ("finops", "user")  # tagged to primary agent
        assert saved[-1] == ("finops", "assistant", body["response"])


# ── Spec 18 T11 ──────────────────────────────────────────────────────────────


class TestSpec18T11InvestigationSmoke:
    """Spec 18 T11: trivial symptom → triage fast path; cross-domain → full RCA."""

    @pytest.mark.asyncio
    async def test_trivial_symptom_skips_investigation(self, harness):
        harness.bedrock.classifier_reply = {
            "agents": [{"agent": "kubernetes", "confidence": 0.93, "sub_query": ""}],
            "reasoning": "k8s listing",
            "complexity": "simple",
        }

        resp, _ = await harness.process("list the pods in namespace payments", session_id="s-18a")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["agent"] == "kubernetes"
        assert "rca" not in body
        assert body["response"] == "kubernetes answer: all good"

        b = harness.bedrock
        assert len(b.by_role("classifier")) == 1, "trivial path is classifier-routed"
        assert [c.agent_id for c in b.by_role("agent")] == ["kubernetes"]
        assert EVIDENCE_QUERY_MARKER not in b.by_role("agent")[0].user_text
        assert b.by_role("synthesis") == [], "no RCA synthesis on the trivial path"
        _assert_message_contract(harness.storage, "s-18a")

    @pytest.mark.parametrize("user_input, mode", [
        ("checkout API is slow, please look at it", "investigate"),   # explicit flag
        ("why did checkout start failing after deploy?", None),       # triage heuristic
    ], ids=["mode-investigate", "triage-heuristic"])
    @pytest.mark.asyncio
    async def test_cross_domain_symptom_runs_full_investigation(self, harness, user_input, mode):
        resp, _ = await harness.process(user_input, mode=mode, session_id="s-18b")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["agent"] == "investigation"

        rca = body["rca"]
        assert set(rca) >= {
            "hypothesis", "confidence", "evidence", "timeline", "contradicting", "prevention",
            "confidence_level", "confidence_track", "independent_signal_count", "temporal_violations",
        }
        assert rca["hypothesis"] == json.loads(_RCA_JSON)["hypothesis"]
        assert body["response"] == rca["hypothesis"]
        assert rca["confidence"] in ("alta", "media", "baixa")
        assert body["confidence"] == rca["confidence"]
        assert rca["confidence_level"] in ("HIGH", "MEDIUM", "LOW")
        assert rca["prevention"] == json.loads(_RCA_JSON)["prevention"]
        assert rca["contradicting"] == []

        b = harness.bedrock
        agent_calls = b.by_role("agent")
        consulted = {c.agent_id for c in agent_calls}
        assert len(agent_calls) == 5 and len(consulted) == 5
        assert all(EVIDENCE_QUERY_MARKER in c.user_text for c in agent_calls)
        # Evidence was parsed per agent (one structured item each), with attribution.
        assert len(rca["evidence"]) == 5
        assert {e["source_agent"] for e in rca["evidence"]} == consulted
        assert all(e["strength"] in ("forte", "media", "fraca") for e in rca["evidence"])
        # Timeline is the temporally ordered evidence.
        stamps = [e["timestamp"] for e in rca["timeline"] if e["timestamp"]]
        assert stamps == sorted(stamps) and stamps
        # Spec 14 E2: evidence spend is charged to the real session.
        assert {c.budget_session_id for c in agent_calls} == {"s-18b"}
        assert all(c.session_id.startswith("s-18b-inv-") for c in agent_calls)

        # Investigation bypasses the classifier and pays for exactly one RCA synthesis.
        assert b.by_role("classifier") == []
        synth = b.by_role("synthesis")
        assert [c.agent_id for c in synth] == ["rca-synthesizer"]
