"""Download engine: streamed, atomic, checksummed, cache-aware, with an ordered fallback chain."""

from __future__ import annotations

import logging
import zipfile
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import httpx

from .manifest import FileRecord, Manifest, sha256_file, utcnow

log = logging.getLogger("sentinel.datasets")

USER_AGENT = "orbital-sentinel-datafetch/0.1 (+https://github.com/Kaushik2210/ORBITAL-SENTINEL)"


class FetchError(RuntimeError):
    """Raised when every source in a fallback chain failed."""


@dataclass(frozen=True)
class Source:
    """One place a file can be fetched from. ``name`` is recorded in the manifest."""

    name: str
    url: str


def make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(60.0, connect=15.0),
        headers={"User-Agent": USER_AGENT},
        transport=transport,
    )


def _is_cached(dest: Path, rel: str, manifest: Manifest) -> bool:
    rec = manifest.files.get(rel)
    if rec is None or not dest.is_file() or dest.stat().st_size != rec.size_bytes:
        return False
    return sha256_file(dest) == rec.sha256


def _stream_to(client: httpx.Client, url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with part.open("wb") as fh:
                for block in resp.iter_bytes(1 << 20):
                    fh.write(block)
        part.replace(dest)
    finally:
        part.unlink(missing_ok=True)


def fetch_file(
    client: httpx.Client,
    sources: Sequence[Source],
    root: Path,
    rel: str,
    manifest: Manifest,
) -> FileRecord:
    """Fetch ``rel`` into ``root`` trying ``sources`` in order; skip if a verified copy exists."""
    dest = root / rel
    if _is_cached(dest, rel, manifest):
        return manifest.files[rel]
    errors: list[str] = []
    for src in sources:
        try:
            _stream_to(client, src.url, dest)
        except (httpx.HTTPError, OSError) as exc:
            log.warning("source %s failed for %s: %s", src.name, rel, exc)
            errors.append(f"{src.name}: {exc}")
            continue
        rec = FileRecord(
            path=rel,
            url=src.url,
            sha256=sha256_file(dest),
            size_bytes=dest.stat().st_size,
            source=src.name,
            fetched_at=utcnow(),
        )
        manifest.record(rec)
        return rec
    raise FetchError(f"all sources failed for {rel}: " + "; ".join(errors))


def fetch_many(
    client: httpx.Client,
    jobs: Sequence[tuple[Sequence[Source], str]],
    root: Path,
    manifest: Manifest,
    workers: int = 8,
    on_done: Callable[[FileRecord], None] | None = None,
) -> list[FileRecord]:
    """Fetch several files concurrently. The manifest is only mutated from the caller thread."""

    def one(job: tuple[Sequence[Source], str]) -> FileRecord:
        sources, rel = job
        scratch = Manifest(files=dict(manifest.files))  # thread-local view; merged below
        return fetch_file(client, sources, root, rel, scratch)

    records: list[FileRecord] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for rec in pool.map(one, jobs):
            manifest.record(rec)
            records.append(rec)
            if on_done:
                on_done(rec)
    return records


def extract_members(archive: Path, dest_root: Path, prefix_strip: str = "") -> list[Path]:
    """Extract a zip into ``dest_root`` refusing path traversal. Returns extracted file paths."""
    out: list[Path] = []
    base = dest_root.resolve()
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.removeprefix(prefix_strip)
            target = (dest_root / name).resolve()
            if not target.is_relative_to(base):
                raise FetchError(f"unsafe path in archive: {info.filename!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                dst.write(src.read())
            out.append(target)
    return out
