"""Fetch engine tests. The network is replaced by httpx.MockTransport (test-only)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
import numpy as np
import pytest

from sentinel_sim.datasets.fetch import FetchError, Source, extract_members, fetch_file, fetch_many
from sentinel_sim.datasets.manifest import Manifest, sha256_file
from sentinel_sim.datasets.sources import (
    HF_BASE,
    LABELS_REL,
    TELEMANOM_LABELS_UPSTREAM,
    TELEMANOM_S3_ZIP,
    UNLABELED_CHANNELS,
    channel_ids,
    fetch_smap_msl,
)


def npy_bytes(rows: int = 4, cols: int = 3) -> bytes:
    buf = io.BytesIO()
    np.save(buf, np.arange(rows * cols, dtype=np.float64).reshape(rows, cols))
    return buf.getvalue()


def make_client(
    routes: dict[str, tuple[int, bytes]], hits: list[str] | None = None
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if hits is not None:
            hits.append(url)
        status, body = routes.get(url, (404, b"not found"))
        return httpx.Response(status, content=body)

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_fetch_file_records_checksum_and_source(tmp_path: Path) -> None:
    client = make_client({"https://a/x.bin": (200, b"hello")})
    manifest = Manifest()
    rec = fetch_file(client, [Source("primary", "https://a/x.bin")], tmp_path, "d/x.bin", manifest)
    assert (tmp_path / "d/x.bin").read_bytes() == b"hello"
    assert rec.size_bytes == 5
    assert rec.sha256 == sha256_file(tmp_path / "d/x.bin")
    assert rec.source == "primary"
    assert manifest.files["d/x.bin"] == rec
    assert not list(tmp_path.rglob("*.part")), "no partial files left behind"


def test_fetch_file_falls_back_to_next_source(tmp_path: Path) -> None:
    client = make_client({"https://mirror/x": (200, b"data")})  # primary 404s
    rec = fetch_file(
        client,
        [Source("primary", "https://primary/x"), Source("mirror", "https://mirror/x")],
        tmp_path,
        "x",
        Manifest(),
    )
    assert rec.source == "mirror"


def test_fetch_file_raises_when_all_sources_fail(tmp_path: Path) -> None:
    client = make_client({})
    with pytest.raises(FetchError, match="all sources failed"):
        fetch_file(client, [Source("a", "https://a/x")], tmp_path, "x", Manifest())
    assert not (tmp_path / "x").exists()
    assert not list(tmp_path.rglob("*.part"))


def test_cached_file_is_not_refetched(tmp_path: Path) -> None:
    hits: list[str] = []
    client = make_client({"https://a/x": (200, b"abc")}, hits)
    manifest = Manifest()
    fetch_file(client, [Source("s", "https://a/x")], tmp_path, "x", manifest)
    fetch_file(client, [Source("s", "https://a/x")], tmp_path, "x", manifest)
    assert hits == ["https://a/x"]


def test_corrupted_cache_is_redownloaded(tmp_path: Path) -> None:
    hits: list[str] = []
    client = make_client({"https://a/x": (200, b"abc")}, hits)
    manifest = Manifest()
    fetch_file(client, [Source("s", "https://a/x")], tmp_path, "x", manifest)
    (tmp_path / "x").write_bytes(b"xyz")  # same size, different content
    fetch_file(client, [Source("s", "https://a/x")], tmp_path, "x", manifest)
    assert (tmp_path / "x").read_bytes() == b"abc"
    assert len(hits) == 2


def test_manifest_round_trip(tmp_path: Path) -> None:
    client = make_client({"https://a/x": (200, b"abc")})
    manifest = Manifest()
    fetch_file(client, [Source("s", "https://a/x")], tmp_path, "x", manifest)
    manifest.save(tmp_path)
    loaded = Manifest.load(tmp_path)
    assert loaded.files["x"].sha256 == manifest.files["x"].sha256


def test_fetch_many_merges_records(tmp_path: Path) -> None:
    routes = {f"https://a/{i}": (200, bytes([i])) for i in range(5)}
    manifest = Manifest()
    jobs = [([Source("s", f"https://a/{i}")], f"f/{i}") for i in range(5)]
    recs = fetch_many(make_client(routes), jobs, tmp_path, manifest, workers=3)
    assert len(recs) == 5
    assert set(manifest.files) == {f"f/{i}" for i in range(5)}


def test_extract_members_blocks_path_traversal(tmp_path: Path) -> None:
    zpath = tmp_path / "evil.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("../escape.txt", "x")
    with pytest.raises(FetchError, match="unsafe path"):
        extract_members(zpath, tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()


LABELS_CSV = (
    "chan_id,spacecraft,anomaly_sequences,class,num_values\n"
    'P-1,SMAP,"[[1, 2]]",[point],10\n'
    'P-2,SMAP,"[[3, 4]]",[point],10\n'
    'P-2,SMAP,"[[5, 6]]",[point],10\n'
    'M-1,MSL,"[[1, 2]]",[contextual],10\n'
)


def test_channel_ids_dedupes_repeated_rows(tmp_path: Path) -> None:
    p = tmp_path / "labels.csv"
    p.write_text(LABELS_CSV, encoding="utf-8")
    assert channel_ids(p) == ["P-1", "P-2", "M-1"]


def _smap_routes(chans: list[str]) -> dict[str, tuple[int, bytes]]:
    routes: dict[str, tuple[int, bytes]] = {
        TELEMANOM_LABELS_UPSTREAM: (200, LABELS_CSV.encode()),
        TELEMANOM_S3_ZIP: (403, b"forbidden"),  # what the real primary returned when probed
    }
    for split in ("train", "test"):
        for ch in chans:
            routes[f"{HF_BASE}/data/data/{split}/{ch}.npy"] = (200, npy_bytes())
    return routes


def test_smap_msl_uses_mirror_when_primary_is_forbidden(tmp_path: Path) -> None:
    chans = ["P-1", "P-2", "M-1", *UNLABELED_CHANNELS]
    manifest = Manifest()
    recs = fetch_smap_msl(make_client(_smap_routes(chans)), tmp_path, manifest)
    assert len(recs) == 1 + 2 * len(chans)  # labels + train/test per channel (incl. unlabeled)
    for split in ("train", "test"):
        for ch in chans:
            assert (tmp_path / "smap_msl" / split / f"{ch}.npy").is_file()
    array_sources = {r.source for r in recs if r.path.endswith(".npy")}
    assert array_sources == {"mirror-hf"}
    assert manifest.files[LABELS_REL].source == "upstream-github"


def test_smap_msl_prefers_primary_archive_when_available(tmp_path: Path) -> None:
    chans = ["P-1", "P-2", "M-1", *UNLABELED_CHANNELS]
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as zf:
        for split in ("train", "test"):
            for ch in chans:
                zf.writestr(f"data/{split}/{ch}.npy", npy_bytes())
    routes = _smap_routes(chans)
    routes[TELEMANOM_S3_ZIP] = (200, zbuf.getvalue())
    hits: list[str] = []
    recs = fetch_smap_msl(make_client(routes, hits), tmp_path, Manifest())
    assert {r.source for r in recs if r.path.endswith(".npy")} == {"primary-s3"}
    assert not any(u.startswith(HF_BASE) and u.endswith(".npy") for u in hits)
    assert not (tmp_path / "smap_msl" / "_data.zip").exists(), "temporary archive removed"
