from __future__ import annotations

from sentinel_agent.offline import investigate_offline

from ._fixtures import make_case


def test_offline_report_names_the_top_evidence_and_stays_within_the_schema() -> None:
    case = make_case()
    report = investigate_offline(case)
    assert report.verdict.value == "cyberattack"
    assert report.confidence == case.summary.confidence
    assert "auth_anomaly" in report.key_evidence[0]
    assert any("offline fallback" in c for c in report.caveats)
    assert "Escalate" in report.recommended_action


def test_offline_report_flags_abstention_as_a_caveat() -> None:
    case = make_case()
    object.__setattr__(case.summary, "verdict", "needs_human")
    report = investigate_offline(case)
    assert report.verdict.value == "needs_human"
    assert any("abstained" in c for c in report.caveats)


def test_offline_report_is_deterministic() -> None:
    case = make_case()
    a, b = investigate_offline(case), investigate_offline(case)
    assert a.model_dump() == b.model_dump()
