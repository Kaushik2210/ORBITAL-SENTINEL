"""Cross-platform task runner. The Makefile delegates here so Windows works without `make`.

Usage: python scripts/tasks.py <task>      (or: make <task>)
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*cmd: str) -> None:
    print(f"$ {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=ROOT, check=False)  # noqa: S603 - fixed argv, no shell
    if result.returncode != 0:
        sys.exit(result.returncode)


def uv(*args: str) -> None:
    run("uv", *args)


def setup() -> None:
    """Create the venv and install runtime + dev dependencies."""
    uv("sync", "--all-groups")


def lint() -> None:
    uv("run", "ruff", "check", ".")
    uv("run", "ruff", "format", "--check", ".")


def format_() -> None:
    uv("run", "ruff", "check", "--fix", ".")
    uv("run", "ruff", "format", ".")


def typecheck() -> None:
    uv("run", "mypy")


def test() -> None:
    uv("run", "pytest", "--cov", "--cov-report=term-missing:skip-covered")


def data() -> None:
    """Download, checksum and cache the real datasets into data/raw/ (profile via DATA_PROFILE)."""
    import os

    uv(
        "run",
        "python",
        "-m",
        "sentinel_sim.datasets",
        "--profile",
        os.environ.get("DATA_PROFILE", "lite"),
    )


def check() -> None:
    """Everything CI runs for Python."""
    lint()
    typecheck()
    test()


TASKS: dict[str, Callable[[], None]] = {
    "setup": setup,
    "lint": lint,
    "format": format_,
    "typecheck": typecheck,
    "test": test,
    "data": data,
    "check": check,
}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in TASKS:
        print("usage: python scripts/tasks.py {" + "|".join(TASKS) + "}", file=sys.stderr)
        return 2
    TASKS[argv[1]]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
