"""The incident taxonomy shared by detectors, attribution, the API and the UI.

Anomalies are first split into *operational* and *security* domains, then attributed to one
of five classes. ``NEEDS_HUMAN`` is a sixth, explicit outcome: the platform is allowed to say
"I cannot separate these hypotheses" instead of forcing a guess.
"""

from __future__ import annotations

from enum import StrEnum


class Domain(StrEnum):
    """Which team owns the response."""

    OPERATIONAL = "operational"
    SECURITY = "security"
    NONE = "none"


class IncidentClass(StrEnum):
    """Posterior classes. Values are stable API identifiers."""

    NOMINAL = "nominal"
    MECHANICAL_FAILURE = "mechanical_failure"
    ENVIRONMENTAL = "environmental"
    SENSOR_MALFUNCTION = "sensor_malfunction"
    CYBERATTACK = "cyberattack"


class Verdict(StrEnum):
    """What the platform finally reports: a class or an explicit abstention."""

    NOMINAL = IncidentClass.NOMINAL.value
    MECHANICAL_FAILURE = IncidentClass.MECHANICAL_FAILURE.value
    ENVIRONMENTAL = IncidentClass.ENVIRONMENTAL.value
    SENSOR_MALFUNCTION = IncidentClass.SENSOR_MALFUNCTION.value
    CYBERATTACK = IncidentClass.CYBERATTACK.value
    NEEDS_HUMAN = "needs_human"


_DOMAIN: dict[IncidentClass, Domain] = {
    IncidentClass.NOMINAL: Domain.NONE,
    IncidentClass.MECHANICAL_FAILURE: Domain.OPERATIONAL,
    IncidentClass.ENVIRONMENTAL: Domain.OPERATIONAL,
    IncidentClass.SENSOR_MALFUNCTION: Domain.OPERATIONAL,
    IncidentClass.CYBERATTACK: Domain.SECURITY,
}


def domain_of(cls: IncidentClass) -> Domain:
    """Return the owning domain for a class (security vs operational)."""
    return _DOMAIN[cls]
