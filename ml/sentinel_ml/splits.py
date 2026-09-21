"""Dataset splits. By variant index, so each split contains every scenario family."""

from __future__ import annotations

TRAIN = frozenset(range(0, 8))
VAL = frozenset({8, 9})
TEST = frozenset(range(10, 14))


def split_of(variant: int) -> str:
    if variant in TRAIN:
        return "train"
    if variant in VAL:
        return "val"
    if variant in TEST:
        return "test"
    raise ValueError(f"variant {variant} is in no split")
