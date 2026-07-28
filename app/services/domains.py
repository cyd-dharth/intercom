import asyncio
import logging
import secrets
from urllib.parse import urlparse

import asyncpg
import dns.resolver

from app.config import settings
from app.logging import log_extra
from app.repositories import domains as domains_repo

logger = logging.getLogger("app.services.domains")

MAX_VERIFY_ATTEMPTS = 10
VERIFY_RETRY_SECONDS = 60
DNS_LOOKUP_TIMEOUT_SECONDS = 5


def cname_target() -> str:
    """The host a customer's CNAME record must point at, per section 12 step 1. Derived
    from APP_BASE_URL rather than hardcoded so it tracks wherever this instance is
    actually deployed."""
    return urlparse(settings.app_base_url).hostname or settings.app_base_url


def verification_txt_name(hostname: str) -> str:
    return f"_inbox-verify.{hostname}"


def new_verification_token() -> str:
    return secrets.token_hex(16)


def dns_records_for(hostname: str, verification_token: str) -> list[dict]:
    """The exact two DNS records a customer must create, per section 12 step 1 and the
    Settings page requirement in step 4."""
    return [
        {"type": "CNAME", "name": hostname, "value": cname_target()},
        {"type": "TXT", "name": verification_txt_name(hostname), "value": verification_token},
    ]


def _resolve_first(name: str, record_type: str) -> list[str]:
    resolver = dns.resolver.Resolver()
    resolver.timeout = DNS_LOOKUP_TIMEOUT_SECONDS
    resolver.lifetime = DNS_LOOKUP_TIMEOUT_SECONDS
    answer = resolver.resolve(name, record_type)
    return [str(r).strip('"') for r in answer]


def check_dns(hostname: str, verification_token: str) -> tuple[bool, str]:
    """Resolves the CNAME and TXT records with dnspython per section 12 step 2. Returns
    (ok, reason). Runs synchronously since dnspython has no asyncio API; callers on the
    event loop must run this in a thread."""
    try:
        cname_values = _resolve_first(hostname, "CNAME")
    except dns.resolver.NXDOMAIN:
        return False, f"no CNAME record found for {hostname}"
    except dns.resolver.NoAnswer:
        return False, f"no CNAME record found for {hostname}"
    except dns.exception.Timeout:
        return False, f"DNS lookup for {hostname} timed out"
    except Exception as exc:
        return False, f"CNAME lookup failed: {exc}"

    expected_target = cname_target().rstrip(".")
    if not any(value.rstrip(".") == expected_target for value in cname_values):
        return False, f"CNAME for {hostname} points to {cname_values}, expected {expected_target}"

    txt_name = verification_txt_name(hostname)
    try:
        txt_values = _resolve_first(txt_name, "TXT")
    except dns.resolver.NXDOMAIN:
        return False, f"no TXT record found at {txt_name}"
    except dns.resolver.NoAnswer:
        return False, f"no TXT record found at {txt_name}"
    except dns.exception.Timeout:
        return False, f"DNS lookup for {txt_name} timed out"
    except Exception as exc:
        return False, f"TXT lookup failed: {exc}"

    if verification_token not in txt_values:
        return False, f"TXT record at {txt_name} does not contain the expected verification token"

    return True, "verified"


async def run_verification(conn: asyncpg.Connection, domain: asyncpg.Record) -> asyncpg.Record | None:
    """Runs one DNS check and updates the row per section 12 step 2. Caller (the
    verify_domain job handler) decides whether to reschedule; this function only ever
    performs a single check and write."""
    ok, reason = await asyncio.to_thread(check_dns, domain["hostname"], domain["verification_token"])
    if ok:
        logger.info("custom domain verified", extra=log_extra(hostname=domain["hostname"]))
        return await domains_repo.mark_verified(conn, domain["id"])

    logger.info("custom domain verification failed this attempt", extra=log_extra(hostname=domain["hostname"], reason=reason))
    return await domains_repo.mark_pending_retry(conn, domain["id"], reason)
