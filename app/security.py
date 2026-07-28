import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.config import settings

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hmac10(value: str) -> str:
    digest = hmac.new(settings.session_secret.encode(), value.encode(), hashlib.sha256).hexdigest()
    return digest[:10]


def verify_hmac10(value: str, tag: str) -> bool:
    return hmac.compare_digest(hmac10(value), tag)


VISITOR_TOKEN_TTL_SECONDS = 30 * 60


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def issue_visitor_token(workspace_id: uuid.UUID, contact_id: uuid.UUID) -> str:
    """Short lived, HMAC signed token scoped to a single widget visitor. Not a JWT library,
    but the same shape: header-less payload plus a signature, since the project avoids
    adding dependencies for a single signed token use case."""
    payload = {
        "workspace_id": str(workspace_id),
        "contact_id": str(contact_id),
        "scope": "visitor",
        "exp": int(time.time()) + VISITOR_TOKEN_TTL_SECONDS,
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64url_encode(hmac.new(settings.session_secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_visitor_token(token: str) -> dict | None:
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        return None
    expected_sig = _b64url_encode(hmac.new(settings.session_secret.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected_sig):
        return None
    try:
        payload = json.loads(_b64url_decode(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if payload.get("scope") != "visitor":
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload
