"""SPARTA and MITRE ATT&CK identifiers used to tag scenarios.

Every ID below was looked up on the official site on 2026-09-21 (SPARTA v4.0.1 at
https://sparta.aerospace.org/, MITRE ATT&CK at https://attack.mitre.org/); none is recalled from
memory. SPARTA has no separate denial-of-service or brute-force technique in the fetched matrix, so
those scenarios use ATT&CK IDs only. The mapping is a *labeling aid for the simulation*, not an
assessment that a scenario reproduces a specific real-world campaign.
"""

from __future__ import annotations

SPARTA: dict[str, str] = {
    "EX-0001/01": "Replay: Command Packets",
    "EX-0001/02": "Replay: Bus Traffic Replay",
    "EX-0007": "Trigger Single Event Upset",
    "EX-0013/01": "Flooding: Valid Commands",
    "EX-0013/02": "Flooding: Erroneous Input",
    "EX-0014/02": "Spoofing: Bus Traffic Spoofing",
    "EX-0014/03": "Spoofing: Sensor Data",
    "EX-0016/01": "Jamming: Uplink Jamming",
    "EX-0016/02": "Jamming: Downlink Jamming",
    "IA-0007/02": "Malicious Commanding via Valid GS",
}

ATTACK: dict[str, str] = {
    "T1110": "Brute Force",
    "T1078": "Valid Accounts",
    "T1498": "Network Denial of Service",
    "T1499": "Endpoint Denial of Service",
    "T1557": "Adversary-in-the-Middle",
    "T1565": "Data Manipulation",
}


def validate(sparta: list[str], attack: list[str]) -> None:
    """Reject any tag that is not in the verified registries above."""
    bad = [t for t in sparta if t not in SPARTA] + [t for t in attack if t not in ATTACK]
    if bad:
        raise ValueError(f"unverified technique ids: {bad}")
