"""Tests for src.core.investigation — Evidence, RCAResult, build_timeline, correlate."""

from src.core.investigation import Evidence, RCAResult, build_timeline, correlate


class TestEvidenceDataclass:
    def test_evidence_dataclass(self):
        e = Evidence(
            source_agent="obs",
            signal_type="metric",
            timestamp="2026-06-14T10:00:00Z",
            strength="forte",
            summary="CPU spike to 95%",
        )
        assert e.source_agent == "obs"
        assert e.signal_type == "metric"
        assert e.timestamp == "2026-06-14T10:00:00Z"
        assert e.strength == "forte"
        assert e.summary == "CPU spike to 95%"
        assert e.is_causal_candidate is False  # default


class TestRCAResultToDict:
    def test_rca_result_to_dict(self):
        ev = Evidence("a", "metric", "2026-01-01T00:00:00Z", "forte", "x")
        rca = RCAResult(
            hypothesis="disk full",
            confidence="alta",
            evidence=[ev],
            timeline=[ev],
            contradicting=[],
            prevention=["add alert"],
        )
        d = rca.to_dict()
        assert set(d.keys()) == {"hypothesis", "confidence", "evidence", "timeline", "contradicting", "prevention"}
        assert d["hypothesis"] == "disk full"
        assert d["confidence"] == "alta"
        assert len(d["evidence"]) == 1
        assert d["prevention"] == ["add alert"]


class TestBuildTimeline:
    def test_build_timeline_sorts_by_timestamp(self):
        e1 = Evidence("a", "metric", "2026-06-14T12:00:00Z", "forte", "late")
        e2 = Evidence("b", "log", "2026-06-14T10:00:00Z", "media", "early")
        e3 = Evidence("c", "event", "2026-06-14T11:00:00Z", "fraca", "mid")
        result = build_timeline([e1, e2, e3])
        assert [e.summary for e in result] == ["early", "mid", "late"]

    def test_build_timeline_excludes_non_temporal(self):
        e1 = Evidence("a", "metric", "2026-06-14T10:00:00Z", "forte", "has time")
        e2 = Evidence("b", "infra", "", "fraca", "no time")
        result = build_timeline([e1, e2])
        assert len(result) == 1
        assert result[0].summary == "has time"

    def test_build_timeline_marks_causal_candidate(self):
        e1 = Evidence("a", "deploy", "2026-06-14T10:00:00Z", "forte", "deploy v2.1 to prod")
        e2 = Evidence("b", "metric", "2026-06-14T10:05:00Z", "media", "CPU normal")
        result = build_timeline([e1, e2])
        assert result[0].is_causal_candidate is True
        assert result[1].is_causal_candidate is False


class TestCorrelate:
    def test_correlate_3_signals_no_contradicting_returns_alta(self):
        evidence = [
            Evidence("obs", "metric", "", "forte", "x"),
            Evidence("aws", "infra", "", "media", "y"),
            Evidence("dev", "log", "", "forte", "z"),
        ]
        assert correlate(evidence, contradicting=[]) == "alta"

    def test_correlate_3_signals_with_contradicting_returns_media(self):
        evidence = [
            Evidence("obs", "metric", "", "forte", "x"),
            Evidence("aws", "infra", "", "media", "y"),
            Evidence("dev", "log", "", "forte", "z"),
        ]
        contradicting = [Evidence("sec", "event", "", "fraca", "contradicts")]
        assert correlate(evidence, contradicting) == "media"

    def test_correlate_2_signals_returns_media(self):
        evidence = [
            Evidence("obs", "metric", "", "forte", "x"),
            Evidence("aws", "infra", "", "media", "y"),
        ]
        assert correlate(evidence, contradicting=[]) == "media"

    def test_correlate_1_signal_returns_baixa(self):
        evidence = [Evidence("obs", "metric", "", "forte", "x")]
        assert correlate(evidence, contradicting=[]) == "baixa"

    def test_correlate_dependent_signals_count_as_one(self):
        evidence = [
            Evidence("obs", "metric", "", "forte", "a"),
            Evidence("obs", "metric", "", "media", "b"),
            Evidence("obs", "metric", "", "fraca", "c"),
        ]
        assert correlate(evidence, contradicting=[]) == "baixa"
