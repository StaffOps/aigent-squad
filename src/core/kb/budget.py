"""Monthly budget cap for distillation pipeline."""
import os
from datetime import datetime, timezone

from src.core.cache import cache
from src.core.logger import logger

MONTHLY_BUDGET_USD = float(os.getenv("KB_MONTHLY_BUDGET_USD", "50.0"))


def _key() -> str:
    yyyymm = datetime.now(timezone.utc).strftime("%Y%m")
    return f"kb:budget:{yyyymm}"


def check_budget(estimated_cost: float) -> bool:
    """Returns True if budget allows the estimated cost. Best-effort (uses Redis)."""
    try:
        current = float(cache.get(_key(), namespace="budget") or 0.0)
        return current + estimated_cost <= MONTHLY_BUDGET_USD
    except Exception as e:
        logger.warning("Budget check failed (allowing)", extra={"error": str(e)})
        return True


def record_cost(cost: float):
    try:
        current = float(cache.get(_key(), namespace="budget") or 0.0)
        cache.set(_key(), current + cost, ttl=2678400, namespace="budget")  # 31 days
    except Exception as e:
        logger.warning("Budget record failed", extra={"error": str(e)})
