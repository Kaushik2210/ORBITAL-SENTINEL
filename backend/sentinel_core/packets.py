"""CCSDS-like space packets with an HMAC authentication tag (ADR 0005).

Wire layout (big-endian)::

    primary header  6 B   version:3 type:1 sec_hdr:1 apid:11 | seq_flags:2 seq_count:14 | length:16
    timestamp       8 B   uint32 seconds + uint32 microseconds since the mission epoch
    payload         n B
    auth tag       16 B   HMAC-SHA256(header || timestamp || payload), truncated

``length`` follows CCSDS: number of octets in the packet data field minus one, where the data
field is everything after the primary header (timestamp + payload + tag).

This is a *simulation* of link authentication, not SDLS. The key is synthetic.
"""

from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass
from enum import IntEnum

PRIMARY_HEADER_LEN = 6
TIMESTAMP_LEN = 8
TAG_LEN = 16
MIN_PACKET_LEN = PRIMARY_HEADER_LEN + TIMESTAMP_LEN + TAG_LEN
MAX_APID = (1 << 11) - 1
SEQ_MODULO = 1 << 14  # the sequence count is 14 bits wide and wraps
MAX_PAYLOAD_LEN = 0xFFFF - TIMESTAMP_LEN - TAG_LEN + 1
UNSEGMENTED = 0b11
_VERSION = 0


class MalformedPacketError(ValueError):
    """The bytes are not a structurally valid packet (as opposed to a valid one with a bad tag)."""


class PacketType(IntEnum):
    TELEMETRY = 0
    TELECOMMAND = 1


@dataclass(frozen=True, slots=True)
class SpacePacket:
    apid: int
    seq_count: int
    ptype: PacketType
    timestamp_us: int
    payload: bytes

    def __post_init__(self) -> None:
        if not 0 <= self.apid <= MAX_APID:
            raise ValueError(f"apid out of range: {self.apid}")
        if not 0 <= self.seq_count < SEQ_MODULO:
            raise ValueError(f"seq_count out of range: {self.seq_count}")
        if not 0 <= self.timestamp_us < (1 << 32) * 1_000_000:
            raise ValueError(f"timestamp_us out of range: {self.timestamp_us}")
        if len(self.payload) > MAX_PAYLOAD_LEN:
            raise ValueError("payload too long")

    @property
    def timestamp_s(self) -> float:
        return self.timestamp_us / 1e6


@dataclass(frozen=True, slots=True)
class DecodedPacket:
    packet: SpacePacket
    auth_ok: bool


def _mac(key: bytes, signed: bytes) -> bytes:
    return hmac.new(key, signed, hashlib.sha256).digest()[:TAG_LEN]


def encode(packet: SpacePacket, key: bytes) -> bytes:
    """Serialize and authenticate ``packet``."""
    data_len = TIMESTAMP_LEN + len(packet.payload) + TAG_LEN
    word0 = (_VERSION << 13) | (int(packet.ptype) << 12) | (1 << 11) | packet.apid
    word1 = (UNSEGMENTED << 14) | packet.seq_count
    header = struct.pack(">HHH", word0, word1, data_len - 1)
    seconds, micros = divmod(packet.timestamp_us, 1_000_000)
    signed = header + struct.pack(">II", seconds, micros) + packet.payload
    return signed + _mac(key, signed)


def decode(raw: bytes, key: bytes) -> DecodedPacket:
    """Parse ``raw``. Structure errors raise; a wrong tag yields ``auth_ok=False``.

    A bad tag is not an exception on purpose: the protocol detector must *see* tampered frames.
    """
    if len(raw) < MIN_PACKET_LEN:
        raise MalformedPacketError(f"too short: {len(raw)} bytes")
    word0, word1, length_field = struct.unpack(">HHH", raw[:PRIMARY_HEADER_LEN])
    if word0 >> 13 != _VERSION:
        raise MalformedPacketError(f"unsupported version {word0 >> 13}")
    if not (word0 >> 11) & 1:
        raise MalformedPacketError("secondary header flag not set")
    if len(raw) != PRIMARY_HEADER_LEN + length_field + 1:
        raise MalformedPacketError(
            f"length field says {PRIMARY_HEADER_LEN + length_field + 1} bytes, got {len(raw)}"
        )
    signed, tag = raw[:-TAG_LEN], raw[-TAG_LEN:]
    seconds, micros = struct.unpack(">II", raw[PRIMARY_HEADER_LEN : PRIMARY_HEADER_LEN + 8])
    if micros >= 1_000_000:
        raise MalformedPacketError(f"microseconds field out of range: {micros}")
    packet = SpacePacket(
        apid=word0 & MAX_APID,
        seq_count=word1 & (SEQ_MODULO - 1),
        ptype=PacketType((word0 >> 12) & 1),
        timestamp_us=seconds * 1_000_000 + micros,
        payload=raw[PRIMARY_HEADER_LEN + TIMESTAMP_LEN : -TAG_LEN],
    )
    return DecodedPacket(packet=packet, auth_ok=hmac.compare_digest(tag, _mac(key, signed)))


def seq_delta(prev: int, cur: int) -> int:
    """Signed distance from ``prev`` to ``cur`` on the 14-bit ring, in [-8192, 8191].

    ``1`` is normal, ``0`` a duplicate, ``> 1`` a gap (lost packets), ``< 0`` a regression.
    Handles wrap: ``seq_delta(16383, 0) == 1``.
    """
    d = (cur - prev) % SEQ_MODULO
    return d - SEQ_MODULO if d >= SEQ_MODULO // 2 else d


def pack_floats(values: list[float]) -> bytes:
    """Telemetry payload: float32 samples in APID channel order."""
    return struct.pack(f">{len(values)}f", *values)


def unpack_floats(payload: bytes) -> list[float]:
    if len(payload) % 4:
        raise MalformedPacketError(f"float payload length {len(payload)} is not a multiple of 4")
    return list(struct.unpack(f">{len(payload) // 4}f", payload))
