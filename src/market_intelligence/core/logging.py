import logging
from contextvars import ContextVar, Token
from typing import Any
from uuid import uuid4

from pythonjsonlogger.json import JsonFormatter

CORRELATION_ID: ContextVar[str] = ContextVar("correlation_id", default="-")
SENSITIVE_KEYS = ("password", "token", "cookie", "secret", "authorization")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if any(marker in key.lower() for marker in SENSITIVE_KEYS)
                else redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = CORRELATION_ID.get()
        if isinstance(record.msg, dict):
            record.msg = redact(record.msg)
        return True


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(ContextFilter())
    handler.setFormatter(
        JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s %(correlation_id)s",
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def bind_correlation_id(value: str | None = None) -> Token[str]:
    return CORRELATION_ID.set(value or str(uuid4()))
