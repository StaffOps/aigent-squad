"""RCA investigation models: Evidence, RCAResult, InvestigationState.

Extended with EVIDENCE-MODEL correlator (spec 18 T12):
- Causal-layer aware confidence scoring (Track A / Track B)
- Derivation-pair independence test
- Temporal order validation
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid

SIGNAL_STRENGTHS = {"forte": 3, "media": 2, "fraca": 1}
CAUSAL_KEYWORDS = ("deploy", "release", "config change", "restart", "rollout", "merge", "upgrade")

# --- Causal layers from EVIDENCE-MODEL.md ---
CAUSAL_LAYERS = frozenset({"change", "mechanism", "impact", "temporal", "elimination"})

# Layer ordering for temporal validation: CHANGE must precede MECHANISM must precede IMPACT.
# TEMPORAL and ELIMINATION are modifiers, not ordered.
_LAYER_ORDER: dict[str, int] = {"change": 0, "mechanism": 1, "impact": 2}

# --- Derivation pairs (spec §6): b derives from a -> count as 1 signal ---
DERIVATION_PAIRS: frozenset[tuple[str, str]] = frozenset({
    ("OOMKill", "pod_restart"),
    ("error_rate_spike", "error_log"),
    ("error_rate_spike", "error_trace"),
    ("error_log", "error_trace"),
    ("latency_spike", "slow_trace"),
    ("memory_growth", "OOMKill"),
    ("node_NotReady", "pod_eviction"),
    ("node_MemoryPressure", "OOMKill"),
    ("cpu_throttle", "latency_spike"),
})


@dataclass
class Evidence:
    """Single piece of evidence collected during an RCA investigation.

    Original fields (backward-compatible): source_agent, signal_type, timestamp,
    strength, summary, is_causal_candidate.

    Extended fields (spec 18 T12, all Optional with defaults for backward compat):
    causal_layer, signal_id, signal_subtype, fault_domain, timestamp_precision,
    raw_query, derivation_source, is_recurring, first_occurrence,
    contradicts_hypothesis, contradiction_explained, trace_id.
    """

    source_agent: str
    signal_type: str        # metric | log | trace | event | deploy | infra
    timestamp: str          # ISO8601 ("" if non-temporal)
    strength: str           # forte | media | fraca
    summary: str
    is_causal_candidate: bool = False  # filled by timeline builder

    # --- Extended fields from EVIDENCE-MODEL.md (T12) ---
    signal_id: str = ""                    # C1..C8, M1..M13, I1..I8, T1..T4, E1..E4
    signal_subtype: str = ""               # e.g. "OOMKill", "pod_restart"
    causal_layer: str = ""                 # change|mechanism|impact|temporal|elimination
    fault_domain: str = ""                 # app:<svc>|infra:<node>|dep:<svc>|net:<comp>
    timestamp_precision: str = ""          # ms|second|15s|60s
    raw_query: str = ""                    # query that produced this (audit trail)
    derivation_source: str | None = None   # id of evidence this was derived FROM
    is_recurring: bool = False             # K8s events: count > 1?
    first_occurrence: str | None = None    # for recurring: the real causal anchor
    contradicts_hypothesis: bool = False   # does this contradict the working hypothesis?
    contradiction_explained: bool = False  # has the contradiction been explained?
    trace_id: str | None = None            # exemplar-bridge detection
    id: str = ""                           # unique within investigation


@dataclass
class RCAResult:
    """Root-cause analysis result with structured confidence scoring."""

    hypothesis: str
    confidence: str         # alta | media | baixa
    evidence: list[Evidence] = field(default_factory=list)
    timeline: list[Evidence] = field(default_factory=list)
    contradicting: list[Evidence] = field(default_factory=list)
    prevention: list[str] = field(default_factory=list)

    # --- Extended fields (T12) ---
    confidence_level: str = ""             # HIGH | MEDIUM | LOW
    confidence_track: str = ""             # A | B | INSUFFICIENT
    independent_signal_count: int = 0
    temporal_violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis": self.hypothesis,
            "confidence": self.confidence,
            "evidence": [e.__dict__ for e in self.evidence],
            "timeline": [e.__dict__ for e in self.timeline],
            "contradicting": [e.__dict__ for e in self.contradicting],
            "prevention": self.prevention,
            "confidence_level": self.confidence_level,
            "confidence_track": self.confidence_track,
            "independent_signal_count": self.independent_signal_count,
            "temporal_violations": self.temporal_violations,
        }


@dataclass
class InvestigationState:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    symptom: str = ""
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    rounds_completed: int = 0
    max_rounds: int = 1
    evidence: list[Evidence] = field(default_factory=list)
    agents_consulted: list[str] = field(default_factory=list)
    agents_failed: list[str] = field(default_factory=list)


def build_timeline(evidence_list: list[Evidence]) -> list[Evidence]:
    """Sort evidence by timestamp ASC; mark causal candidates (deploy/restart/config).

    Non-temporal evidence (timestamp='') is excluded from timeline (kept in evidence pool).
    """
    temporal = [e for e in evidence_list if e.timestamp]
    temporal.sort(key=lambda e: e.timestamp)
    for e in temporal:
        summary_lower = e.summary.lower()
        if any(kw in summary_lower for kw in CAUSAL_KEYWORDS):
            e.is_causal_candidate = True
    return temporal


def correlate(evidence_list: list[Evidence], contradicting: list[Evidence]) -> str:
    """Returns confidence: 'alta' | 'media' | 'baixa'.

    Phase 1 simplified rule:
    - Count distinct (source_agent, signal_type) pairs as 'independent signals'
    - >=3 independent + 0 contradicting = alta
    - 2 independent OR (3+ but 1+ contradicting) = media
    - 1 independent OR (2 with contradicting) = baixa
    """
    independent_signals = {(e.source_agent, e.signal_type) for e in evidence_list}
    n = len(independent_signals)
    has_contradicting = len(contradicting) > 0

    if n >= 3 and not has_contradicting:
        return "alta"
    if n >= 3 and has_contradicting:
        return "media"
    if n == 2:
        return "baixa" if has_contradicting else "media"
    return "baixa"


# ---------------------------------------------------------------------------
# EVIDENCE-MODEL correlator (spec 18 T12)
# ---------------------------------------------------------------------------


def _are_derived(a: Evidence, b: Evidence) -> bool:
    """Check if evidence b is derived from evidence a (or vice versa).

    Uses signal_subtype matching against DERIVATION_PAIRS, plus explicit
    derivation_source links and shared trace_id (exemplar bridge).
    """
    # Explicit derivation link
    if b.derivation_source and b.derivation_source == a.id:
        return True
    if a.derivation_source and a.derivation_source == b.id:
        return True

    # Shared trace_id = same observation at different zoom (exemplar bridge)
    if a.trace_id and b.trace_id and a.trace_id == b.trace_id:
        return True

    # Static derivation pairs (subtype matching)
    pair_ab = (a.signal_subtype, b.signal_subtype)
    pair_ba = (b.signal_subtype, a.signal_subtype)
    if pair_ab in DERIVATION_PAIRS or pair_ba in DERIVATION_PAIRS:
        # Additional check: same fault_domain makes them non-independent
        if a.fault_domain and b.fault_domain and a.fault_domain == b.fault_domain:
            return True

    return False


def count_independent(evidences: list[Evidence]) -> int:
    """Count truly independent signals after removing derivation pairs.

    Independence rules from EVIDENCE-MODEL §6:
    1. Same trace_id or derivation link -> NOT independent
    2. Same layer + same fault_domain -> NOT independent (default)
    3. Same fault_domain + in DERIVATION_PAIRS -> NOT independent
    4. Different layer + different fault_domain -> INDEPENDENT
    5. Different layer + same fault_domain + not derivable -> INDEPENDENT
    6. Same layer + different fault_domain -> INDEPENDENT
    """
    if not evidences:
        return 0

    # Build adjacency: mark pairs that are NOT independent
    n = len(evidences)
    # Use union-find to group non-independent evidence
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for i in range(n):
        for j in range(i + 1, n):
            a, b = evidences[i], evidences[j]

            # Rule 1: explicit derivation or trace_id bridge
            if _are_derived(a, b):
                union(i, j)
                continue

            # Rule 2: same layer + same fault_domain -> NOT independent
            if (
                a.causal_layer
                and b.causal_layer
                and a.causal_layer == b.causal_layer
                and a.fault_domain
                and b.fault_domain
                and a.fault_domain == b.fault_domain
            ):
                union(i, j)
                continue

    # Count distinct groups
    roots = {find(i) for i in range(n)}
    return len(roots)


def validate_temporal_order(evidences: list[Evidence]) -> list[str]:
    """Check that CHANGE signals precede MECHANISM which precede IMPACT.

    Returns list of violation descriptions. Empty list = valid ordering.
    Only considers evidence with both causal_layer and timestamp set.
    """
    violations: list[str] = []

    # Collect timestamped evidence per ordered layer
    layered: dict[str, list[Evidence]] = {"change": [], "mechanism": [], "impact": []}
    for e in evidences:
        if e.causal_layer in layered and e.timestamp:
            layered[e.causal_layer].append(e)

    # Find earliest timestamp per layer
    def _earliest_ts(layer_evidences: list[Evidence]) -> str | None:
        timestamps = [e.timestamp for e in layer_evidences if e.timestamp]
        return min(timestamps) if timestamps else None

    change_earliest = _earliest_ts(layered["change"])
    mechanism_earliest = _earliest_ts(layered["mechanism"])
    impact_earliest = _earliest_ts(layered["impact"])

    # Validate: CHANGE must precede MECHANISM
    if change_earliest and mechanism_earliest:
        if change_earliest > mechanism_earliest:
            violations.append(
                f"Temporal violation: earliest CHANGE ({change_earliest}) "
                f"is AFTER earliest MECHANISM ({mechanism_earliest})"
            )

    # Validate: MECHANISM must precede IMPACT
    if mechanism_earliest and impact_earliest:
        if mechanism_earliest > impact_earliest:
            violations.append(
                f"Temporal violation: earliest MECHANISM ({mechanism_earliest}) "
                f"is AFTER earliest IMPACT ({impact_earliest})"
            )

    # Validate: CHANGE must precede IMPACT
    if change_earliest and impact_earliest:
        if change_earliest > impact_earliest:
            violations.append(
                f"Temporal violation: earliest CHANGE ({change_earliest}) "
                f"is AFTER earliest IMPACT ({impact_earliest})"
            )

    return violations


def score_confidence(evidences: list[Evidence]) -> tuple[str, str]:
    """Score RCA confidence using causal-layer logic from EVIDENCE-MODEL §5.

    Returns:
        (confidence_level, track) where:
        - confidence_level: "HIGH" | "MEDIUM" | "LOW"
        - track: "A" | "B" | "INSUFFICIENT"

    Scoring rules:
    - Track A: CHANGE + MECHANISM + IMPACT + valid temporal + 0 unexplained contradictions → HIGH
    - Track B: continuous MECHANISM + confirming MECHANISM + IMPACT + no CHANGE → HIGH
    - Less than 3 independent signals → LOW
    - Unexplained contradiction present → caps at MEDIUM
    - Temporal order invalid → LOW (blocker)
    """
    if not evidences:
        return ("LOW", "INSUFFICIENT")

    # Classify by layer
    layers_present: set[str] = set()
    has_change = False
    mechanism_count = 0
    has_impact = False
    unexplained_contradictions = 0

    for e in evidences:
        if e.causal_layer:
            layers_present.add(e.causal_layer)
        if e.causal_layer == "change":
            has_change = True
        if e.causal_layer == "mechanism":
            mechanism_count += 1
        if e.causal_layer == "impact":
            has_impact = True
        if e.contradicts_hypothesis and not e.contradiction_explained:
            unexplained_contradictions += 1

    # Blocker: temporal order invalid
    temporal_violations = validate_temporal_order(evidences)
    if temporal_violations:
        return ("LOW", "INSUFFICIENT")

    # Blocker: unexplained contradiction caps at MEDIUM
    capped_at_medium = unexplained_contradictions > 0

    # Independence count
    independent = count_independent(evidences)

    # Less than 3 independent signals → LOW
    if independent < 3:
        return ("LOW", "INSUFFICIENT")

    # Track A: CHANGE + MECHANISM + IMPACT
    if has_change and mechanism_count >= 1 and has_impact:
        if capped_at_medium:
            return ("MEDIUM", "A")
        return ("HIGH", "A")

    # Track B: >=2 MECHANISM (continuous + confirming) + IMPACT + no CHANGE
    if not has_change and mechanism_count >= 2 and has_impact:
        if capped_at_medium:
            return ("MEDIUM", "B")
        return ("HIGH", "B")

    # Partial coverage: 2 of 3 layers → MEDIUM
    ordered_layers = {"change", "mechanism", "impact"}
    present_ordered = layers_present & ordered_layers
    if len(present_ordered) >= 2:
        return ("MEDIUM", "A" if has_change else "B")

    return ("LOW", "INSUFFICIENT")
