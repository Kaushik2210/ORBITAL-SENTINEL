"""The ground segment's legitimate configuration, as detectors need to know it.

In a real deployment this is the mission database and operations roster. Here it is derived from the
synthetic side-channel generators, so the detectors and the generators agree on what is "normal".
"""

from __future__ import annotations

from sentinel_core.detection.protocol import MissionKnowledge
from sentinel_core.timebase import STEP_SECONDS

from .sidechannels import GROUND_STATIONS, OPCODES, OPERATORS, in_contact


def mission_knowledge() -> MissionKnowledge:
    return MissionKnowledge(
        opcodes={code: name for name, code in OPCODES.items()},
        stations=frozenset(GROUND_STATIONS),
        operators=frozenset(OPERATORS),
        in_contact=lambda ts: in_contact(int(ts // STEP_SECONDS)),
    )
