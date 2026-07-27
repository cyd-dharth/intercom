import hashlib
import hmac
import secrets

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
