"""Degradation trajectories from the real PCoE battery and IMS bearing data, plus labeled fallbacks.

Both loaders return a ``source`` string: ``"pcoe"`` when derived from the real files, or
``"synthetic-parametric"`` when the data is unavailable. Downstream code carries that string into
the ``synthetic`` flags, so fallback data can never be presented as real (ADR 0003, ADR 0004).
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .smap_msl import DataUnavailableError

CANONICAL_CELLS = ("B0005", "B0006", "B0007", "B0018")  # clean 24 C aging cells (DATASETS.md)
BATTERY_ZIP = "pcoe_battery/5.+Battery+Data+Set.zip"
BEARINGS_ZIP = "pcoe_bearings/4.+Bearings.zip"
IMS_TEST2_SNAPSHOTS = 984


@dataclass(frozen=True, slots=True)
class BatteryTrajectory:
    """Aging trajectory on a normalized progress axis ``p in [0, 1]`` (0 = new, 1 = end of data)."""

    cell: str
    source: str
    progress_cap: NDArray[np.float64]
    capacity_frac: NDArray[np.float64]  # capacity / first-cycle capacity
    progress_re: NDArray[np.float64]
    re_ohm: NDArray[np.float64]  # electrolyte resistance from impedance cycles
    capacity0_ah: float

    def capacity_at(self, p: float) -> float:
        return float(np.interp(p, self.progress_cap, self.capacity_frac))

    def resistance_at(self, p: float) -> float:
        return float(np.interp(p, self.progress_re, self.re_ohm))


@dataclass(frozen=True, slots=True)
class WheelTrajectory:
    """Per-snapshot vibration features for 4 bearings; bearing index 0 is the one that fails."""

    source: str
    rms: NDArray[np.float64]  # (n, 4)
    kurtosis: NDArray[np.float64]  # (n, 4)
    failing_bearing: int = 0


# --------------------------------------------------------------------------- battery


def _scalar(value: Any) -> float | None:
    arr = np.atleast_1d(np.asarray(value, dtype=float)).ravel()
    return float(arr[0]) if arr.size == 1 and np.isfinite(arr[0]) else None


def _read_cell_bytes(zip_path: Path, cell: str) -> bytes:
    with zipfile.ZipFile(zip_path) as outer:
        for name in outer.namelist():
            if not name.endswith(".zip"):
                continue
            with zipfile.ZipFile(io.BytesIO(outer.read(name))) as inner:
                for member in inner.namelist():
                    if Path(member).stem == cell and member.endswith(".mat"):
                        return inner.read(member)
    raise DataUnavailableError(f"{cell} not found in {zip_path}")


def load_battery(root: Path, cell: str = "B0005") -> BatteryTrajectory:
    """Real capacity and resistance trajectory of one PCoE cell.

    Falls back to :func:`parametric_battery` (labeled synthetic) if the archive is missing.
    """
    zip_path = root / BATTERY_ZIP
    if not zip_path.is_file():
        return parametric_battery(cell)
    from scipy.io import loadmat  # local import: scipy is heavy and only needed here

    blob = _read_cell_bytes(zip_path, cell)
    with tempfile.TemporaryDirectory() as tmp:
        mat = Path(tmp) / f"{cell}.mat"
        mat.write_bytes(blob)
        data = loadmat(str(mat), squeeze_me=True, struct_as_record=False)
    cycles = data[cell].cycle
    n = len(cycles)
    cap_p: list[float] = []
    caps: list[float] = []
    re_p: list[float] = []
    res: list[float] = []
    for i, c in enumerate(cycles):
        prog = i / max(1, n - 1)
        if c.type == "discharge" and (cap := _scalar(c.data.Capacity)) is not None:
            cap_p.append(prog)
            caps.append(cap)
        elif c.type == "impedance" and (re := _scalar(c.data.Re)) is not None and re > 0:
            re_p.append(prog)
            res.append(re)
    if len(caps) < 2 or len(res) < 2:
        raise DataUnavailableError(f"{cell}: not enough usable cycles")
    return BatteryTrajectory(
        cell=cell,
        source="pcoe",
        progress_cap=np.array(cap_p),
        capacity_frac=np.array(caps) / caps[0],
        progress_re=np.array(re_p),
        re_ohm=np.array(res),
        capacity0_ah=caps[0],
    )


def parametric_battery(cell: str = "B0005", seed: int = 0) -> BatteryTrajectory:
    """Documented fallback: linear-plus-knee capacity fade and growing resistance.

    Magnitudes (about -28 % capacity, resistance x1.22) are set to match the *measured* endpoints of
    cell B0005 (see docs/DATASETS.md) but the curve shape is invented, so this is **not** a
    measurement. Always labeled ``synthetic-parametric``.
    """
    p = np.linspace(0.0, 1.0, 60)
    rng = np.random.default_rng(
        int.from_bytes(hashlib.sha256(f"{cell}:{seed}".encode()).digest()[:8])
    )
    cap = 1.0 - 0.22 * p - 0.06 * np.clip((p - 0.7) / 0.3, 0, 1) ** 2 + rng.normal(0, 0.002, p.size)
    re = 0.0454 * (1.0 + 0.22 * p**1.5) + rng.normal(0, 0.0004, p.size)
    return BatteryTrajectory(cell, "synthetic-parametric", p, cap, p, re, 2.0)


# --------------------------------------------------------------------------- bearings


def _rar_extractor() -> list[str] | None:
    """Find a tool that can read RAR: bsdtar/libarchive tar, unrar, or 7z. Returns argv prefix."""
    system32_tar = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "tar.exe"
    # Git-for-Windows shells put GNU tar first on PATH and it cannot read RAR: look for bsdtar.
    candidates = [shutil.which("bsdtar"), str(system32_tar) if system32_tar.is_file() else None]
    candidates.append(shutil.which("tar"))
    for path in candidates:
        if path is None:
            continue
        out = subprocess.run([path, "--version"], capture_output=True, text=True, check=False)  # noqa: S603
        if "bsdtar" in out.stdout.lower() + out.stderr.lower():
            return [path, "-xf"]
    if path := shutil.which("unrar"):
        return [path, "x", "-inul"]
    if path := shutil.which("7z"):
        return [path, "x", "-y"]
    return None


def _snapshot_features(files: list[Path]) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    from scipy.stats import kurtosis

    rms = np.empty((len(files), 4))
    kurt = np.empty((len(files), 4))
    for i, f in enumerate(files):
        x = np.loadtxt(f, dtype=np.float64)
        rms[i] = np.sqrt((x**2).mean(axis=0))
        kurt[i] = np.asarray(kurtosis(x, axis=0, fisher=False))
    return rms, kurt


def load_wheel(root: Path, cache_dir: Path | None = None) -> WheelTrajectory:
    """Vibration features of IMS test 2 (bearing 1 fails). Cached after the first extraction.

    Extraction needs py7zr plus a RAR-capable tool; without them (or without the download) this
    returns the labeled synthetic-parametric trajectory.
    """
    cache = (cache_dir or root.parent / "cache") / "ims_test2_features.npz"
    if cache.is_file():
        z = np.load(cache)
        return WheelTrajectory("pcoe", z["rms"], z["kurtosis"])
    zip_path = root / BEARINGS_ZIP
    extractor = _rar_extractor()
    if not zip_path.is_file() or extractor is None:
        return parametric_wheel()
    import py7zr

    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extract("4. Bearings/IMS.7z", tmp)
        with py7zr.SevenZipFile(tmp / "4. Bearings" / "IMS.7z") as sz:
            sz.extract(path=tmp / "x", targets=["2nd_test.rar"])
        subprocess.run([*extractor, str(tmp / "x" / "2nd_test.rar")], cwd=tmp / "x", check=True)  # noqa: S603
        files = sorted(p for p in (tmp / "x" / "2nd_test").iterdir() if p.is_file())
        if len(files) != IMS_TEST2_SNAPSHOTS:
            raise DataUnavailableError(
                f"expected {IMS_TEST2_SNAPSHOTS} snapshots, got {len(files)}"
            )
        rms, kurt = _snapshot_features(files)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, rms=rms, kurtosis=kurt)
    return WheelTrajectory("pcoe", rms, kurt)


def parametric_wheel(seed: int = 0, n: int = IMS_TEST2_SNAPSHOTS) -> WheelTrajectory:
    """Documented fallback: flat healthy RMS with mild drift; bearing 0 wears exponentially.

    Not a measurement. Always labeled ``synthetic-parametric``.
    """
    rng = np.random.default_rng(seed)
    t = np.linspace(0, 1, n)
    base = np.array([0.078, 0.095, 0.105, 0.056])
    rms = base * (1 + 0.3 * t[:, None]) * (1 + rng.normal(0, 0.03, (n, 4)))
    rms[:, 0] *= 1 + 1.8 * np.exp(6 * (t - 1))  # accelerating wear on bearing 0
    kurt = 3.3 + rng.normal(0, 0.15, (n, 4))
    kurt[:, 0] += 1.3 * np.exp(6 * (t - 1))
    return WheelTrajectory("synthetic-parametric", rms, kurt)
