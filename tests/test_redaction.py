import io
import logging

from wilmington_pay.redaction import JsonFormatter, RedactingFilter, redact, redact_mapping


def test_redact_pan_variants():
    for raw in ("4111111111111111", "4111 1111 1111 1111", "4111-1111-1111-1111"):
        out = redact(f"card {raw} end")
        assert "4111" not in out.split("...")[0] and out.endswith("...1111] end")


def test_short_numbers_untouched():
    assert redact("SR-2026-104233 code 482913 amount 5000") == "SR-2026-104233 code 482913 amount 5000"


def test_redact_mapping_masks_sensitive_keys():
    out = redact_mapping({"action": "charge", "encCardNumber": "AYADe...", "encCvv": "x",
                          "encExpiry": "y", "sessionId": "abc", "cardLast4": "1111"})
    assert out["encCardNumber"] == out["encCvv"] == out["encExpiry"] == "***"
    assert out["sessionId"] == "abc" and out["cardLast4"] == "1111"


def test_logger_pipeline_scrubs_accidental_pan():
    stream = io.StringIO()
    logger = logging.getLogger("redaction-test")
    h = logging.StreamHandler(stream)
    h.setFormatter(JsonFormatter())
    h.addFilter(RedactingFilter())
    logger.addHandler(h)
    logger.propagate = False
    logger.warning("oops %s", "5555555555554444")
    assert "5555555555554444" not in stream.getvalue()
