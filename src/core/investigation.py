"""RCA investigation models: Evidence, RCAResult, InvestigationState."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid

SIGNAL_STRENGTHS = {"forte": 3, "media": 2, "fraca": 1}
CAUSAL_KEYWORDS = ("deploy", "release", "config change", "restart", "rollout", "merge", "upgrade")


@dataclass
class Evidence:
    source_agent: str
    signal_type: str        # metric | log | trace | event | deploy | infra
    timestamp: str          # ISO8601 ("" if non-temporal)
    strength: str           # forte | media | fraca
    summary: str
    is_causal_candidate: bool = False  # filled by timeline builder


@dataclass
class RCAResult:
    hypothesis: str
    confidence: str         # alta | media | baixa
    evidence: list[Evidence] = field(default_factory=list)
    timeline: list[Evidence] = field(default_factory=list)
    contradicting: list[Evidence] = field(default_factory=list)
    prevention: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "hypothesis": self.hypothesis,
            "confidence": self.confidence,
            "evidence": [e.__dict__ for e in self.evidence],
            "timeline": [e.__dict__ for e in self.timeline],
            "contradicting": [e.__dict__ for e in self.contradicting],
            "prevention": self.prevention,
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
