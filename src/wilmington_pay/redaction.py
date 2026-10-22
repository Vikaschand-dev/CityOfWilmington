"""Defence-in-depth log redaction.

Code should never log card data in the first place; this filter is the
safety net that scrubs anything resembling a PAN before it reaches
CloudWatch (PCI DSS req. 3.3 / 3.4).
"""
from __future__ import annotations

import json
import logging
import re
import sys

# 13-19 digits, optionally separated by single spaces or dashes
_PAN_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_SENSITIVE_KEYS = re.compile(r"(card|pan|cvv|cvc|exp|security.?code)", re.IGNORECASE)


def redact(text: str) -> str:
    def _mask(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        return f"[PAN-REDACTED ...{digits[-4:]}]"

    return _PAN_RE.sub(_mask, text)


def redact_mapping(data: dict) -> dict:
    """Scrub values of sensitive-looking keys (e.g. Connect event Parameters)."""
    out = {}
    for k, v in data.items():
        if isinstance(v, dict):
            out[k] = redact_mapping(v)
        elif _SENSITIVE_KEYS.search(str(k)) and k not in {"cardLast4", "cardBrand", "card_last4", "card_brand"}:
            out[k] = "***"
        else:
            out[k] = redact(v) if isinstance(v, str) else v
    return out


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        record.msg = redact(msg)
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict):
            payload.update(redact_mapping(extra))
        return redact(json.dumps(payload, default=str))


class _StderrHandler(logging.StreamHandler):
    """Resolves sys.stderr at write time (Lambda runtime / test capture safe)."""

    @property
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, _value):
        pass


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not getattr(logger, "_cow_configured", False):
        handler = _StderrHandler()
        handler.setFormatter(JsonFormatter())
        handler.addFilter(RedactingFilter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger._cow_configured = True  # type: ignore[attr-defined]
    return logger
