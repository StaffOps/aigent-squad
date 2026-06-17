import redis
import json
from typing import Any, Optional
from src.core.config import settings
from src.core.logger import logger


class CacheStore:
    """ElastiCache Redis with fail-open resilience"""

    def __init__(self):
        try:
            self.redis = redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                ssl=settings.redis_ssl,
                password=settings.redis_password,
                decode_responses=True
            )
        except Exception as e:
            logger.warning("Redis connection failed (fail-open)", extra={"error": str(e)})
            self.redis = None

    def get(self, key: str, namespace: str = "default") -> Optional[Any]:
        try:
            if not self.redis:
                return None
            full_key = f"{namespace}:{key}"
            value = self.redis.get(full_key)
            return json.loads(value) if value else None
        except Exception as e:
            logger.warning("Redis unavailable (fail-open)", extra={"error": str(e), "key": key})
            return None

    def set(self, key: str, value: Any, ttl: int, namespace: str = "default"):
        try:
            if not self.redis:
                return
            full_key = f"{namespace}:{key}"
            self.redis.setex(full_key, ttl, json.dumps(value))
        except Exception as e:
            logger.warning("Redis set failed (fail-open)", extra={"error": str(e), "key": key})

    def delete(self, key: str, namespace: str = "default"):
        try:
            if not self.redis:
                return
            full_key = f"{namespace}:{key}"
            self.redis.delete(full_key)
        except Exception as e:
            logger.warning("Redis delete failed (fail-open)", extra={"error": str(e), "key": key})

    def exists(self, key: str, namespace: str = "default") -> bool:
        try:
            if not self.redis:
                return False
            full_key = f"{namespace}:{key}"
            return self.redis.exists(full_key) > 0
        except Exception as e:
            logger.warning("Redis exists failed (fail-open)", extra={"error": str(e), "key": key})
            return False


cache = CacheStore()
