"""The final structured investigation report.

This is the only way an investigation can end: the model must call the ``submit_report`` tool
(built from this schema, see ``client.py``) with arguments that pass validation here, or the
offline fallback fills it in from the same evidence instead. Free-text output alone is never
accepted as an answer, which closes off the obvious prompt-injection goal of getting the model to
just *say* a verdict in prose.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from sentinel_core.taxonomy import Verdict


class Report(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1, max_length=2000)
    key_evidence: list[str] = Field(min_length=1, max_length=10)
    reasoning: list[str] = Field(default_factory=list, max_length=20)
    recommended_action: str = Field(min_length=1, max_length=500)
    caveats: list[str] = Field(default_factory=list, max_length=10)
