"""Password hashing shared by the current account registry."""

import hashlib
import hmac
import secrets

AUTH_COOKIE = "audiblecalendar_session"
PASSWORD_ITERATIONS = 600_000
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"
MIN_PASSWORD_LENGTH = 12


def validate_new_password(password: str) -> None:
    if not MIN_PASSWORD_LENGTH <= len(password) <= 256 or password == DEFAULT_PASSWORD:
        raise ValueError("Choose a password between 12 and 256 characters")


def set_session_cookie(response, request, token: str) -> None:
    """Use the scheme resolved by Uvicorn's trusted-proxy handling."""
    response.set_cookie(
        AUTH_COOKIE, token, max_age=30 * 24 * 60 * 60, httponly=True,
        secure=request.url.scheme == "https", samesite="lax", path="/",
    )


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    return digest.hex(), salt.hex()


def hash_password(password: str) -> tuple[str, str]:
    """Return a password hash and salt for account stores."""

    return _hash_password(password)


def verify_password(password: str, password_hash: str, password_salt: str) -> bool:
    """Verify a password against a stored PBKDF2 hash and salt."""

    try:
        candidate, _ = _hash_password(password, bytes.fromhex(password_salt))
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(candidate, password_hash)
