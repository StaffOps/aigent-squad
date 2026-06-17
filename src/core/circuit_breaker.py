import time
from enum import Enum

from src.core.metrics import circuit_breaker_transitions


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int = 5, recovery_timeout: float = 30.0):
        self.name = name
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.last_failure_time = 0.0

    def can_execute(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                circuit_breaker_transitions.add(1, {"name": self.name, "from": "open", "to": "half_open"})
                return True
            return False
        # HALF_OPEN: allow one attempt
        return True

    def record_success(self):
        if self.state == CircuitState.HALF_OPEN:
            circuit_breaker_transitions.add(1, {"name": self.name, "from": "half_open", "to": "closed"})
        self.failure_count = 0
        self.state = CircuitState.CLOSED

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            circuit_breaker_transitions.add(1, {"name": self.name, "from": "closed", "to": "open"})

    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN
