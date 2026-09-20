import struct

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sentinel_core import packets as p

KEY = b"test-key-not-a-secret"


def make(apid: int = 0x101, seq: int = 5, payload: bytes = b"\x00\x01\x02\x03") -> p.SpacePacket:
    return p.SpacePacket(apid, seq, p.PacketType.TELEMETRY, 1_700_000_000_123_456, payload)


packets = st.builds(
    p.SpacePacket,
    apid=st.integers(0, p.MAX_APID),
    seq_count=st.integers(0, p.SEQ_MODULO - 1),
    ptype=st.sampled_from(list(p.PacketType)),
    timestamp_us=st.integers(0, (1 << 32) * 1_000_000 - 1),
    payload=st.binary(max_size=512),
)


@given(packets)
def test_round_trip(packet: p.SpacePacket) -> None:
    decoded = p.decode(p.encode(packet, KEY), KEY)
    assert decoded.packet == packet
    assert decoded.auth_ok


@given(packets, st.data())
def test_any_single_byte_flip_is_detected(packet: p.SpacePacket, data: st.DataObject) -> None:
    raw = bytearray(p.encode(packet, KEY))
    i = data.draw(st.integers(0, len(raw) - 1))
    raw[i] ^= data.draw(st.integers(1, 255))
    try:
        decoded = p.decode(bytes(raw), KEY)
    except p.MalformedPacketError:
        return  # structurally invalid is also a detection
    assert not decoded.auth_ok


def test_wrong_key_fails_auth_but_still_parses() -> None:
    decoded = p.decode(p.encode(make(), KEY), b"another-key")
    assert not decoded.auth_ok
    assert decoded.packet == make()


def test_header_fields_land_in_the_right_bits() -> None:
    raw = p.encode(make(apid=0x7FF, seq=0x3FFF), KEY)
    word0, word1, length = struct.unpack(">HHH", raw[:6])
    assert word0 >> 13 == 0  # version
    assert (word0 >> 12) & 1 == 0  # telemetry
    assert (word0 >> 11) & 1 == 1  # secondary header present
    assert word0 & 0x7FF == 0x7FF
    assert word1 >> 14 == 0b11  # unsegmented
    assert word1 & 0x3FFF == 0x3FFF
    assert length + 1 == len(raw) - 6


def test_telecommand_bit() -> None:
    pk = p.SpacePacket(1, 0, p.PacketType.TELECOMMAND, 0, b"")
    raw = p.encode(pk, KEY)
    assert (struct.unpack(">H", raw[:2])[0] >> 12) & 1 == 1


@pytest.mark.parametrize("n", [0, 5, p.MIN_PACKET_LEN - 1])
def test_truncated_is_malformed(n: int) -> None:
    with pytest.raises(p.MalformedPacketError):
        p.decode(p.encode(make(), KEY)[:n], KEY)


def test_length_field_mismatch_is_malformed() -> None:
    with pytest.raises(p.MalformedPacketError, match="length field"):
        p.decode(p.encode(make(), KEY) + b"\x00", KEY)


def test_bad_version_is_malformed() -> None:
    raw = bytearray(p.encode(make(), KEY))
    raw[0] |= 0b0010_0000
    with pytest.raises(p.MalformedPacketError, match="version"):
        p.decode(bytes(raw), KEY)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"apid": p.MAX_APID + 1},
        {"apid": -1},
        {"seq_count": p.SEQ_MODULO},
        {"timestamp_us": -1},
    ],
)
def test_out_of_range_fields_rejected(kwargs: dict[str, int]) -> None:
    base = {"apid": 1, "seq_count": 0, "timestamp_us": 0}
    base.update(kwargs)
    with pytest.raises(ValueError, match="out of range"):
        p.SpacePacket(
            base["apid"], base["seq_count"], p.PacketType.TELEMETRY, base["timestamp_us"], b""
        )


@pytest.mark.parametrize(
    ("prev", "cur", "expected"),
    [
        (10, 11, 1),  # normal
        (10, 10, 0),  # duplicate
        (10, 14, 4),  # gap of 3 lost packets
        (10, 9, -1),  # regression
        (16383, 0, 1),  # wrap is normal, not a regression
        (0, 16383, -1),  # backwards across the wrap
        (10, 10 + 8191, 8191),
        (10, 10 + 8192, -8192),  # half the ring: ambiguous, reported as regression
    ],
)
def test_seq_delta(prev: int, cur: int, expected: int) -> None:
    assert p.seq_delta(prev % p.SEQ_MODULO, cur % p.SEQ_MODULO) == expected


@given(st.integers(0, p.SEQ_MODULO - 1), st.integers(1, 100))
def test_seq_delta_of_forward_steps_is_the_step_even_across_the_wrap(start: int, step: int) -> None:
    assert p.seq_delta(start, (start + step) % p.SEQ_MODULO) == step


@given(st.lists(st.floats(width=32, allow_nan=False, allow_infinity=False), max_size=64))
def test_float_payload_round_trip(values: list[float]) -> None:
    assert p.unpack_floats(p.pack_floats(values)) == values


def test_float_payload_must_be_multiple_of_four() -> None:
    with pytest.raises(p.MalformedPacketError):
        p.unpack_floats(b"\x00\x00\x00")
