"""Tests for bx_scholar_core.logging."""

from __future__ import annotations

import logging

from bx_scholar_core.logging import get_logger, setup_logging


class TestSetupLogging:
    def test_console_format(self) -> None:
        setup_logging(level="DEBUG", fmt="console")
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert len(root.handlers) == 1

    def test_json_format(self) -> None:
        setup_logging(level="INFO", fmt="json")
        root = logging.getLogger()
        assert root.level == logging.INFO

    def test_httpx_silenced(self) -> None:
        setup_logging(level="DEBUG")
        assert logging.getLogger("httpx").level == logging.WARNING

    def test_get_logger_returns_usable_logger(self) -> None:
        setup_logging()
        logger = get_logger("test.module")
        # structlog returns a lazy proxy that wraps BoundLogger
        assert hasattr(logger, "info")
        assert hasattr(logger, "warning")
        assert hasattr(logger, "error")
