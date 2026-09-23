"""Auth, roles, demo-mode read access, rate limiting, security headers and the audit chain."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from sentinel_api.app import create_app
from sentinel_api.db.engine import make_engine, make_session_factory
from sentinel_api.db.models import AuditLog
from sentinel_api.db.repo import upsert_user
from sentinel_api.security.audit import AuditLedger
from sentinel_api.security.passwords import hash_password, verify_password
from sentinel_api.security.tokens import decode_token, issue_token
from sentinel_api.settings import Settings

pytestmark = pytest.mark.slow

SECRET = "unit-test-secret-at-least-32-bytes-long-000"


def settings(tmp: Path, **over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": f"sqlite+aiosqlite:///{(tmp / 'api.sqlite').as_posix()}",
        "data_root": Path("nonexistent"),
        "l2_models_dir": Path("nonexistent"),
        "donki_cache": tmp / "donki",
        "cors_origins": ["http://localhost:3000"],
        "anthropic_api_key": None,
        "jwt_secret": SECRET,
    }
    base.update(over)
    return Settings(**base)


async def _make_user(cfg: Settings, email: str, role: str, password: str) -> None:
    engine = make_engine(cfg.database_url)
    factory = make_session_factory(engine)
    await upsert_user(factory, email=email, role=role, password_hash=hash_password(password))
    await engine.dispose()


# ------------------------------------------------------------------ passwords and tokens (units)


def test_password_hash_round_trips_and_rejects_wrong_password() -> None:
    encoded = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong password", encoded)


def test_password_hash_is_salted_differently_each_time() -> None:
    a, b = hash_password("same password"), hash_password("same password")
    assert a != b


def test_malformed_password_hash_is_rejected_not_crashed_on() -> None:
    assert not verify_password("anything", "not-a-real-hash")


def test_token_round_trips_and_rejects_tampering() -> None:
    token = issue_token("a@b.com", "analyst", SECRET)
    principal = decode_token(token, SECRET)
    assert principal.email == "a@b.com"
    assert principal.role == "analyst"
    with pytest.raises(Exception):  # noqa: B017, PT011 - any PyJWTError subclass
        decode_token(token, "a different secret entirely, also 32+ bytes")
    with pytest.raises(ValueError, match="not_a_role"):
        issue_token("a@b.com", "not_a_role", SECRET)


# ------------------------------------------------------------------ API-level auth


def test_login_rejects_unknown_user_and_wrong_password(tmp_path: Path) -> None:
    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as c:
        asyncio.run(_make_user(cfg, "u@test.local", "viewer", "right-password"))
        assert (
            c.post(
                "/api/v1/auth/login", json={"email": "nobody@test.local", "password": "x"}
            ).status_code
            == 401
        )
        assert (
            c.post(
                "/api/v1/auth/login", json={"email": "u@test.local", "password": "wrong"}
            ).status_code
            == 401
        )
        ok = c.post(
            "/api/v1/auth/login", json={"email": "u@test.local", "password": "right-password"}
        )
        assert ok.status_code == 200
        assert ok.json()["role"] == "viewer"


def test_me_reports_the_authenticated_principal(tmp_path: Path) -> None:
    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as c:
        asyncio.run(_make_user(cfg, "v@test.local", "viewer", "pw"))
        token = c.post(
            "/api/v1/auth/login", json={"email": "v@test.local", "password": "pw"}
        ).json()["access_token"]
        assert c.get("/api/v1/auth/me").status_code == 401
        r = c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json() == {"email": "v@test.local", "role": "viewer"}


def test_demo_mode_allows_anonymous_reads_but_never_writes(tmp_path: Path) -> None:
    cfg = settings(tmp_path, public_demo_mode=True)
    with TestClient(create_app(cfg)) as c:
        assert c.get("/api/v1/channels").status_code == 200
        assert c.post("/api/v1/sessions", json={"scenario_id": "nominal_a"}).status_code == 401


def test_without_demo_mode_anonymous_reads_are_also_rejected(tmp_path: Path) -> None:
    cfg = settings(tmp_path, public_demo_mode=False)
    with TestClient(create_app(cfg)) as c:
        assert c.get("/api/v1/channels").status_code == 401


def test_viewer_role_cannot_start_a_session_or_read_the_audit_log(tmp_path: Path) -> None:
    cfg = settings(tmp_path, public_demo_mode=False)
    with TestClient(create_app(cfg)) as c:
        asyncio.run(_make_user(cfg, "viewer@test.local", "viewer", "pw"))
        token = c.post(
            "/api/v1/auth/login", json={"email": "viewer@test.local", "password": "pw"}
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert c.get("/api/v1/channels", headers=headers).status_code == 200
        started = c.post("/api/v1/sessions", json={"scenario_id": "nominal_a"}, headers=headers)
        assert started.status_code == 403
        assert c.get("/api/v1/audit", headers=headers).status_code == 403


def test_analyst_role_can_start_a_session_but_not_read_the_audit_log(tmp_path: Path) -> None:
    cfg = settings(tmp_path, public_demo_mode=False)
    with TestClient(create_app(cfg)) as c:
        asyncio.run(_make_user(cfg, "analyst@test.local", "analyst", "pw"))
        token = c.post(
            "/api/v1/auth/login", json={"email": "analyst@test.local", "password": "pw"}
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        r = c.post(
            "/api/v1/sessions", json={"scenario_id": "nominal_a", "speed": 600}, headers=headers
        )
        assert r.status_code == 202
        assert c.get("/api/v1/audit", headers=headers).status_code == 403


def test_expired_or_garbage_tokens_are_rejected(tmp_path: Path) -> None:
    cfg = settings(tmp_path, public_demo_mode=False)
    with TestClient(create_app(cfg)) as c:
        bad = {"Authorization": "Bearer not-a-real-jwt"}
        assert c.get("/api/v1/channels", headers=bad).status_code == 401


# ------------------------------------------------------------------ audit ledger


def test_audit_chain_detects_tampering(tmp_path: Path) -> None:
    cfg = settings(tmp_path)
    engine = make_engine(cfg.database_url)
    factory = make_session_factory(engine)

    async def go() -> None:
        from sentinel_api.db.engine import create_all

        await create_all(engine)
        ledger = AuditLedger(factory)
        await ledger.record("a@b.com", "login", "auth", {"success": True})
        await ledger.record("a@b.com", "session.create", "sess-1", {"kind": "scenario"})
        await ledger.record("a@b.com", "session.control", "sess-1", {"action": "pause"})

        ok, bad = await ledger.verify()
        assert ok
        assert bad is None

        async with factory() as db:
            row = (await db.execute(select(AuditLog).order_by(AuditLog.id))).scalars().first()
            assert row is not None
            bad_id = row.id
            row.detail = {"success": False}  # tamper with a row already in the chain
            await db.commit()

        ok2, bad2 = await ledger.verify()
        assert not ok2
        assert bad2 == bad_id

        await engine.dispose()

    asyncio.run(go())


def test_login_and_session_actions_are_recorded_in_the_audit_log(tmp_path: Path) -> None:
    cfg = settings(tmp_path, public_demo_mode=False)
    with TestClient(create_app(cfg)) as c:
        asyncio.run(_make_user(cfg, "admin@test.local", "admin", "pw"))
        token = c.post(
            "/api/v1/auth/login", json={"email": "admin@test.local", "password": "pw"}
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        c.post("/api/v1/sessions", json={"scenario_id": "nominal_a", "speed": 600}, headers=headers)

        page = c.get("/api/v1/audit", headers=headers).json()
        actions = [row["action"] for row in page["items"]]
        assert "session.create" in actions
        assert actions.count("login") >= 1

        verify = c.get("/api/v1/audit/verify", headers=headers).json()
        assert verify == {"ok": True, "first_bad_row": None}


# ------------------------------------------------------------------ headers and rate limiting


def test_security_headers_are_present_and_docs_are_exempt_from_csp(tmp_path: Path) -> None:
    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as c:
        r = c.get("/api/v1/health")
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert "content-security-policy" in r.headers

        docs = c.get("/docs")
        assert "content-security-policy" not in docs.headers


def test_rate_limit_returns_429_once_exceeded(tmp_path: Path) -> None:
    cfg = settings(tmp_path, rate_limit_per_minute=3)
    with TestClient(create_app(cfg)) as c:
        statuses = [c.get("/api/v1/health").status_code for _ in range(5)]
        assert statuses[:3] == [200, 200, 200]
        assert 429 in statuses


def test_rate_limit_of_zero_disables_it(tmp_path: Path) -> None:
    cfg = settings(tmp_path, rate_limit_per_minute=0)
    with TestClient(create_app(cfg)) as c:
        assert all(c.get("/api/v1/health").status_code == 200 for _ in range(20))
