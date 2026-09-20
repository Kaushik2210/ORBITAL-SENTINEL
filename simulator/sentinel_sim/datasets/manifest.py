"""Checksum manifest for everything cached under ``data/raw/``.

The manifest is trust-on-first-use: the first successful fetch records a SHA-256; later runs
re-hash the cached file and re-download on mismatch. Remote revisions are pinned where the
host allows it (see ``sources.py``) so "first use" is reproducible.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

MANIFEST_NAME = "MANIFEST.json"


class FileRecord(BaseModel):
    path: str = Field(description="POSIX path relative to the raw-data root")
    url: str
    sha256: str
    size_bytes: int
    source: str = Field(description="Which source in the fallback chain served this file")
    fetched_at: datetime


class Manifest(BaseModel):
    version: int = 1
    files: dict[str, FileRecord] = Field(default_factory=dict)

    @classmethod
    def load(cls, root: Path) -> Manifest:
        path = root / MANIFEST_NAME
        if not path.exists():
            return cls()
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        tmp = root / (MANIFEST_NAME + ".part")
        payload = json.loads(self.model_dump_json())
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(root / MANIFEST_NAME)

    def record(self, rec: FileRecord) -> None:
        self.files[rec.path] = rec


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def utcnow() -> datetime:
    return datetime.now(UTC)
