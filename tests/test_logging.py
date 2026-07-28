import logging

from market_intelligence.core.logging import ContextFilter, redact


def test_redact_sensitive_values_recursively() -> None:
    assert redact({"token": "secret", "nested": {"password": "secret"}}) == {
        "token": "[REDACTED]",
        "nested": {"password": "[REDACTED]"},
    }


def test_context_filter_redacts_dict_message() -> None:
    record = logging.LogRecord("test", logging.INFO, "", 0, {"cookie": "secret"}, (), None)
    assert ContextFilter().filter(record)
    assert record.msg == {"cookie": "[REDACTED]"}
