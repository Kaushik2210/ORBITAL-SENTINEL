import pytest

from sentinel_core import __version__
from sentinel_core.taxonomy import Domain, IncidentClass, Verdict, domain_of


def test_version_is_semver() -> None:
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_every_class_has_a_domain() -> None:
    for cls in IncidentClass:
        assert isinstance(domain_of(cls), Domain)


def test_only_cyberattack_is_security() -> None:
    security = [c for c in IncidentClass if domain_of(c) is Domain.SECURITY]
    assert security == [IncidentClass.CYBERATTACK]


def test_verdict_covers_all_classes_plus_abstention() -> None:
    assert {v.value for v in Verdict} == {c.value for c in IncidentClass} | {"needs_human"}


@pytest.mark.parametrize("cls", list(IncidentClass))
def test_verdict_round_trips_from_class_value(cls: IncidentClass) -> None:
    assert Verdict(cls.value).value == cls.value
