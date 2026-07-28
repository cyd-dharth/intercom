import imaplib
import logging
import smtplib

from app.config import settings

logger = logging.getLogger("app.email.client")


def send_smtp(to_addr: str, mime_bytes: bytes) -> None:
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        smtp.starttls()
        smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.sendmail(settings.smtp_user, [to_addr], mime_bytes)


def open_imap() -> imaplib.IMAP4_SSL:
    conn = imaplib.IMAP4_SSL(settings.imap_host)
    conn.login(settings.smtp_user, settings.smtp_password)
    conn.select("INBOX")
    return conn
