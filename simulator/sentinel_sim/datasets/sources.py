"""Where each real dataset comes from, and how to fetch it.

SMAP/MSL fallback chain (see ``docs/DATASETS.md`` and ADR 0003):

1. the original Telemanom S3 archive (returned HTTP 403 when probed on 2026-09-20);
2. the Hugging Face mirror ``appleparan/telemanom`` (BSD-3-Clause), pinned to a commit;
3. (Phase 3) a deterministic synthetic generator, always labeled ``synthetic``.
"""

from __future__ import annotations

import csv
import logging
import zipfile
from pathlib import Path

import httpx

from .fetch import FetchError, Source, fetch_file, fetch_many
from .manifest import FileRecord, Manifest, sha256_file, utcnow

log = logging.getLogger("sentinel.datasets")

# --- SMAP / MSL (Hundman et al., KDD 2018) -------------------------------------------------
TELEMANOM_S3_ZIP = "https://s3-us-west-2.amazonaws.com/telemanom/data.zip"
TELEMANOM_LABELS_UPSTREAM = (
    "https://raw.githubusercontent.com/khundman/telemanom/master/labeled_anomalies.csv"
)
HF_REVISION = "2d22e1061be83a88b7b9e48df35163d5147adc9d"
HF_BASE = f"https://huggingface.co/datasets/appleparan/telemanom/resolve/{HF_REVISION}"

SMAP_MSL_DIR = "smap_msl"
LABELS_REL = f"{SMAP_MSL_DIR}/labeled_anomalies.csv"
# Observed 2026-09-20: the mirror ships arrays for 82 channels but labeled_anomalies.csv has no row
# for T-10 (and lists P-2 twice). T-10 is fetched so the array set is complete, but it is UNLABELED
# and must be excluded from event-level scoring. See docs/DATASETS.md.
UNLABELED_CHANNELS = ("T-10",)

# --- NASA PCoE ------------------------------------------------------------------------------
PCOE_BATTERY_URL = "https://phm-datasets.s3.amazonaws.com/NASA/5.+Battery+Data+Set.zip"
PCOE_BEARINGS_URL = "https://phm-datasets.s3.amazonaws.com/NASA/4.+Bearings.zip"
PCOE_BATTERY_REL = "pcoe_battery/5.+Battery+Data+Set.zip"
PCOE_BEARINGS_REL = "pcoe_bearings/4.+Bearings.zip"


def channel_ids(labels_csv: Path) -> list[str]:
    """Channel ids listed in ``labeled_anomalies.csv`` (one row per anomaly sequence, deduped)."""
    with labels_csv.open(newline="", encoding="utf-8") as fh:
        seen: dict[str, None] = {}
        for row in csv.DictReader(fh):
            seen.setdefault(row["chan_id"], None)
    return list(seen)


def _try_primary_zip(client: httpx.Client, root: Path, manifest: Manifest) -> bool:
    """Attempt the original S3 archive. Returns True if train/test arrays were extracted."""
    rel = f"{SMAP_MSL_DIR}/_data.zip"
    try:
        rec = fetch_file(client, [Source("primary-s3", TELEMANOM_S3_ZIP)], root, rel, manifest)
    except FetchError as exc:
        log.info("primary S3 archive unavailable (%s); using mirror", exc)
        return False
    zpath = root / rel
    with zipfile.ZipFile(zpath) as zf:
        for info in zf.infolist():
            parts = Path(info.filename).parts
            if info.is_dir() or not info.filename.endswith(".npy"):
                continue
            split = "train" if "train" in parts else "test" if "test" in parts else None
            if split is None:
                continue
            dest = root / SMAP_MSL_DIR / split / Path(info.filename).name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(info))
            rel_file = dest.relative_to(root).as_posix()
            manifest.record(
                FileRecord(
                    path=rel_file,
                    url=f"{rec.url}!{info.filename}",
                    sha256=sha256_file(dest),
                    size_bytes=dest.stat().st_size,
                    source="primary-s3",
                    fetched_at=utcnow(),
                )
            )
    zpath.unlink(missing_ok=True)
    manifest.files.pop(rel, None)
    return True


def fetch_smap_msl(client: httpx.Client, root: Path, manifest: Manifest) -> list[FileRecord]:
    """Fetch labels and per-channel train/test arrays; returns all their manifest records."""
    labels = fetch_file(
        client,
        [
            Source("upstream-github", TELEMANOM_LABELS_UPSTREAM),
            Source("mirror-hf", f"{HF_BASE}/labeled_anomalies.csv"),
        ],
        root,
        LABELS_REL,
        manifest,
    )
    chans = [*channel_ids(root / LABELS_REL), *UNLABELED_CHANNELS]

    rels = [f"{SMAP_MSL_DIR}/{s}/{c}.npy" for s in ("train", "test") for c in chans]
    if any(rel not in manifest.files or not (root / rel).is_file() for rel in rels):
        _try_primary_zip(client, root, manifest)
    # One verified pass: valid cached files are skipped; the rest come from the pinned mirror.
    jobs = [
        ([Source("mirror-hf", f"{HF_BASE}/data/data/{s}/{c}.npy")], f"{SMAP_MSL_DIR}/{s}/{c}.npy")
        for s in ("train", "test")
        for c in chans
    ]
    return [labels, *fetch_many(client, jobs, root, manifest)]


def fetch_pcoe_battery(client: httpx.Client, root: Path, manifest: Manifest) -> list[FileRecord]:
    return [
        fetch_file(client, [Source("pcoe-s3", PCOE_BATTERY_URL)], root, PCOE_BATTERY_REL, manifest)
    ]


def fetch_pcoe_bearings(client: httpx.Client, root: Path, manifest: Manifest) -> list[FileRecord]:
    """The IMS bearing archive is one solid 7z (~1 GB); only the `full` profile downloads it."""
    return [
        fetch_file(
            client, [Source("pcoe-s3", PCOE_BEARINGS_URL)], root, PCOE_BEARINGS_REL, manifest
        )
    ]
