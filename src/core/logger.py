"""Structured JSON logging with OTel trace context correlation.

OTel setup is handled by otel-helper (setup_telemetry). This module only
provides the logger instance and helper functions.
"""
import logging
import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict
from opentelemetry import trace

_STANDARD_ATTRS = set(logging.makeLogRecord({}).__dict__.keys()) | {"message", "asctime"}


class JSONFormatter(logging.Formatter):
    """JSON formatter with automatic trace context injection."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        for k, v in record.__dict__.items():
            if k not in _STANDARD_ATTRS and not k.startswith("_"):
                log_data[k] = v

        span = trace.get_current_span()
        if span.is_recording():
            ctx = span.get_span_context()
            log_data["trace_id"] = format(ctx.trace_id, "032x")
            log_data["span_id"] = format(ctx.span_id, "016x")

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


def setup_logger(name: str = "agent-squad", level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = setup_logger()


def log_request(agent_id: str, user_id: str, session_id: str, input_text: str):
    logger.info("Request received", extra={
        "agent_id": agent_id, "user_id": user_id,
        "session_id": session_id, "input_length": len(input_text),
    })


def log_response(agent_id: str, user_id: str, session_id: str, response_length: int, duration_ms: float):
    logger.info("Response sent", extra={
        "agent_id": agent_id, "user_id": user_id,
        "session_id": session_id, "response_length": response_length,
        "duration_ms": duration_ms,
    })


def log_error(agent_id: str, error: Exception, **kwargs):
    logger.error(f"Error in {agent_id}", extra={
        "agent_id": agent_id, "error_type": type(error).__name__,
        "error_message": str(error), **kwargs,
    }, exc_info=True)
