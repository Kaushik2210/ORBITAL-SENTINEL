"""Nonparametric dynamic thresholding and pruning (Hundman et al., KDD 2018, section 3.3).

Given smoothed prediction errors ``e_s``, choose the threshold ``eps = mu + z * sigma`` (z from a
grid) that maximizes the relative drop in mean and standard deviation obtained by removing values
above it, penalized by how many values and how many separate sequences are flagged::

    score(eps) = (d_mu / mu + d_sigma / sigma) / (|E_a| + |E_seq|**2)

Pruning then drops flagged sequences whose peak error is not clearly above the next-highest one (a
relative gap under ``min_drop``), which removes most false positives from noisy stretches.

This works on the error series *itself* (no separate calibration), exactly as the paper does, so it
is usable both offline over a whole test array and online over a trailing window.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

Z_GRID = tuple(np.arange(2.5, 12.0, 0.5))
MIN_DROP = 0.13  # paper's default minimum relative drop between consecutive sequence maxima


def ewma(errors: NDArray[np.float64], span: int = 30) -> NDArray[np.float64]:
    """Exponentially weighted moving average (adjust=False), used to smooth raw errors."""
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(errors, dtype=np.float64)
    acc = float(errors[0]) if len(errors) else 0.0
    for i, x in enumerate(errors):
        acc = alpha * float(x) + (1 - alpha) * acc
        out[i] = acc
    return out


def _sequences(mask: NDArray[np.bool_]) -> list[tuple[int, int]]:
    """Inclusive (start, end) index pairs of contiguous True runs."""
    if not mask.any():
        return []
    idx = np.flatnonzero(mask)
    breaks = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate(([idx[0]], idx[breaks + 1]))
    ends = np.concatenate((idx[breaks], [idx[-1]]))
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def choose_epsilon(e_s: NDArray[np.float64]) -> float | None:
    """The threshold maximizing the paper's score, or None if the series is flat."""
    mu, sigma = float(e_s.mean()), float(e_s.std())
    if sigma <= 1e-12 or mu <= 0:
        return None
    best_score, best_eps = -np.inf, None
    for z in Z_GRID:
        eps = mu + float(z) * sigma
        below = e_s[e_s <= eps]
        above = e_s > eps
        if len(below) == 0 or not above.any():
            continue
        d_mu = (mu - float(below.mean())) / mu
        s_below = float(below.std())
        d_sigma = (sigma - s_below) / sigma
        n_seq = len(_sequences(above))
        score = (d_mu + d_sigma) / (int(above.sum()) + n_seq**2)
        if score > best_score:
            best_score, best_eps = score, eps
    return best_eps


def prune(
    e_s: NDArray[np.float64], seqs: list[tuple[int, int]], eps: float, min_drop: float = MIN_DROP
) -> list[tuple[int, int]]:
    """Keep sequences whose peaks stand clearly above both the others and the nominal maximum."""
    if not seqs:
        return []
    peaks = [float(e_s[a : b + 1].max()) for a, b in seqs]
    nominal_max = float(e_s[e_s <= eps].max()) if (e_s <= eps).any() else 0.0
    order = sorted(range(len(seqs)), key=lambda i: -peaks[i])
    ranked = [peaks[i] for i in order] + [nominal_max]
    keep_upto = 0  # number of top-ranked sequences to keep
    for i in range(len(ranked) - 1):
        if ranked[i] > 0 and (ranked[i] - ranked[i + 1]) / ranked[i] >= min_drop:
            keep_upto = i + 1
    kept = sorted(order[:keep_upto])
    return [seqs[i] for i in kept]


def anomalous_sequences(
    e_s: NDArray[np.float64], window: int = 2100, min_drop: float = MIN_DROP
) -> list[tuple[int, int]]:
    """Anomalous sequences over a whole error series, thresholded per non-overlapping window."""
    out: list[tuple[int, int]] = []
    for start in range(0, len(e_s), window):
        chunk = e_s[start : start + window]
        if len(chunk) < 20:
            continue
        eps = choose_epsilon(chunk)
        if eps is None:
            continue
        seqs = prune(chunk, _sequences(chunk > eps), eps, min_drop)
        out += [(a + start, b + start) for a, b in seqs]
    return merge(out)


def merge(seqs: list[tuple[int, int]], gap: int = 0) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in sorted(seqs):
        if out and a <= out[-1][1] + 1 + gap:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out
