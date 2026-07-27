import contextvars
import json
import logging
import sys
import time
import uuid

from app.config import settings

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
workspace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("workspace_id", default="-")

_REDACT_KEYS = {"password", "token", "body", "email", "authorization", "cookie"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
            "workspace_id": workspace_id_var.get(),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            for key, value in extra.items():
                if key.lower() in _REDACT_KEYS:
                    continue
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(settings.log_level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def log_extra(**fields) -> dict:
    return {"extra_fields": fields}
