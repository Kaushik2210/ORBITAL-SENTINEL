"""Password hashing: PBKDF2-HMAC-SHA256, stdlib only (no extra dependency for something this
small). Not bcrypt/argon2 — documented as a known trade-off in docs/THREAT_MODEL.md; PBKDF2 with a
high iteration count is still an accepted choice (NIST SP 800-63B) even if argon2id is preferred
for new systems.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 600_000  # OWASP 2023 minimum recommendation for PBKDF2-HMAC-SHA256
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iterations_s, salt_hex, digest_hex = encoded.split("$")
        if algo != _ALGO:
            return False
        iterations = int(iterations_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)
