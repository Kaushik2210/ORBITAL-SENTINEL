"""Create or update a user for password login. There is no self-registration endpoint — accounts
are provisioned out of band, which is normal for a small operator-facing tool like this one.

Usage: ``python -m sentinel_api.db.create_user --email you@example.com --role admin``
(prompts are not available in every environment this runs in, so the password is read from the
``USER_PASSWORD`` environment variable or the ``--password`` flag — never logged either way).
"""

from __future__ import annotations

import argparse
import asyncio
import os

from sentinel_api.security.passwords import hash_password
from sentinel_api.security.tokens import ROLE_RANK

from .engine import make_engine, make_session_factory
from .repo import upsert_user


async def _run(email: str, role: str, password: str) -> str:
    engine = make_engine()
    factory = make_session_factory(engine)
    uid = await upsert_user(factory, email=email, role=role, password_hash=hash_password(password))
    await engine.dispose()
    return uid


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--email", required=True)
    ap.add_argument("--role", choices=sorted(ROLE_RANK), default="admin")
    ap.add_argument("--password", default=None, help="falls back to $USER_PASSWORD")
    args = ap.parse_args()
    password = args.password or os.environ.get("USER_PASSWORD")
    if not password:
        raise SystemExit("pass --password or set USER_PASSWORD")
    uid = asyncio.run(_run(args.email, args.role, password))
    print(f"ok: {args.email} ({args.role}) id={uid}")


if __name__ == "__main__":
    main()
