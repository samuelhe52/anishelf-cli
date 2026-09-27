from __future__ import annotations

import logging
import sys
import time

from anishelf_cli.core.redaction import SecretRedactor

LOGGER_NAME = "anishelf_cli"
_HANDLER_MARKER = "_anishelf_cli_diagnostic_handler"


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        redactor = getattr(record, "redactor", None)
        if isinstance(redactor, SecretRedactor):
            message = redactor.redact(message)
        return f"[debug] {message}"


def configure_logging(*, verbose: bool) -> None:
    logger = logging.getLogger(LOGGER_NAME)
    logger.propagate = False
    logger.setLevel(logging.DEBUG if verbose else logging.CRITICAL + 1)

    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            logger.removeHandler(handler)
            handler.close()

    if not verbose:
        return

    handler = logging.StreamHandler(sys.stderr)
    setattr(handler, _HANDLER_MARKER, True)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(RedactingFormatter())
    logger.addHandler(handler)


def elapsed_ms(started: float) -> str:
    """Format the time since a `time.perf_counter()` reading for diagnostics."""
    return f"{(time.perf_counter() - started) * 1000:.0f}ms"


def get_logger(name: str) -> logging.Logger:
    if name == LOGGER_NAME or name.startswith(f"{LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
