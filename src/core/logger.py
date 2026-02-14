import logging
import json
import sys
from datetime import datetime
from typing import Any, Dict
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.logging import LoggingInstrumentor

# Setup OpenTelemetry
resource = Resource.create({"service.name": "agent-squad"})
trace.set_tracer_provider(TracerProvider(resource=resource))
tracer = trace.get_tracer(__name__)

# Console exporter for now (will be replaced with OTLP collector later)
span_processor = BatchSpanProcessor(ConsoleSpanExporter())
trace.get_tracer_provider().add_span_processor(span_processor)

# Instrument logging
LoggingInstrumentor().instrument(set_logging_format=True)

class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging"""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }
        
        # Add extra fields
        if hasattr(record, 'extra'):
            log_data.update(record.extra)
        
        # Add trace context if available
        span = trace.get_current_span()
        if span.is_recording():
            span_context = span.get_span_context()
            log_data["trace_id"] = format(span_context.trace_id, '032x')
            log_data["span_id"] = format(span_context.span_id, '016x')
        
        # Add exception info
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_data)

def setup_logger(name: str = "agent-squad", level: str = "INFO") -> logging.Logger:
    """Setup structured JSON logger with OpenTelemetry"""
    
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))
    
    # Remove existing handlers
    logger.handlers.clear()
    
    # Console handler with JSON formatter
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    
    # Prevent propagation to root logger
    logger.propagate = False
    
    return logger

# Global logger instance
logger = setup_logger()

# Helper functions for structured logging
def log_with_context(level: str, message: str, **kwargs):
    """Log with additional context"""
    getattr(logger, level)(message, extra=kwargs)

def log_request(agent_id: str, user_id: str, session_id: str, input_text: str):
    """Log incoming request"""
    logger.info("Request received", extra={
        "agent_id": agent_id,
        "user_id": user_id,
        "session_id": session_id,
        "input_length": len(input_text)
    })

def log_response(agent_id: str, user_id: str, session_id: str, response_length: int, duration_ms: float):
    """Log response"""
    logger.info("Response sent", extra={
        "agent_id": agent_id,
        "user_id": user_id,
        "session_id": session_id,
        "response_length": response_length,
        "duration_ms": duration_ms
    })

def log_error(agent_id: str, error: Exception, **kwargs):
    """Log error with context"""
    logger.error(f"Error in {agent_id}", extra={
        "agent_id": agent_id,
        "error_type": type(error).__name__,
        "error_message": str(error),
        **kwargs
    }, exc_info=True)
