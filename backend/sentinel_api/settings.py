"""Runtime configuration, read from the environment (see ``.env.example``). No secrets live here."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _list(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "DATABASE_URL", "sqlite+aiosqlite:///./data/sentinel.sqlite"
        )
    )
    data_root: Path = field(default_factory=lambda: Path(os.environ.get("DATA_ROOT", "data/raw")))
    scenarios_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("SCENARIOS_DIR", "data/scenarios"))
    )
    attribution_model: Path = field(
        default_factory=lambda: Path(
            os.environ.get("ATTRIBUTION_MODEL", "models/attribution-v1.json")
        )
    )
    l2_models_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("L2_MODELS_DIR", "ml/runs/l2-v1/onnx"))
    )
    docs_data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("DOCS_DATA_DIR", "docs/data"))
    )
    donki_cache: Path = field(
        default_factory=lambda: Path(os.environ.get("DONKI_CACHE", "data/cache/donki"))
    )
    cors_origins: list[str] = field(
        default_factory=lambda: _list("CORS_ALLOW_ORIGINS", "http://localhost:3000")
    )
    calibration_steps: int = field(
        default_factory=lambda: int(os.environ.get("CALIBRATION_STEPS", "1800"))
    )
    max_concurrent_sessions: int = field(
        default_factory=lambda: int(os.environ.get("MAX_CONCURRENT_SESSIONS", "4"))
    )
