"""The AI investigation agent: a read-only Claude tool-use loop over one incident's evidence,
with a deterministic offline fallback. See ``docs/adr/0011-investigation-agent.md``.

This package never touches a database or the network on its own — ``case.py`` defines the data
bundle its tools read, and the caller (``sentinel_api``) is the one with DB access, so the
read-only guarantee is a type signature, not a prompting convention.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
