from __future__ import annotations

import pytest
from pydantic import ValidationError

from sentinel_agent.report import Report


def _base(**over: object) -> dict[str, object]:
    d: dict[str, object] = {
        "verdict": "cyberattack",
        "confidence": 0.5,
        "summary": "x",
        "key_evidence": ["e1"],
        "recommended_action": "escalate",
    }
    d.update(over)
    return d


def test_a_minimal_valid_report_round_trips() -> None:
    r = Report.model_validate(_base())
    assert r.verdict.value == "cyberattack"
    assert r.reasoning == []
    assert r.caveats == []


@pytest.mark.parametrize(
    "override",
    [
        {"verdict": "not_a_real_class"},
        {"confidence": 1.5},
        {"confidence": -0.1},
        {"key_evidence": []},
        {"summary": ""},
        {"surprise_field": "nope"},
    ],
)
def test_invalid_reports_are_rejected(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Report.model_validate(_base(**override))
