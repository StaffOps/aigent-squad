import redis
import json
from typing import Any, Optional
from src.core.config import settings

class CacheStore:
    """ElastiCache Redis for high-performance caching"""
    
    def __init__(self):
        self.redis = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            ssl=settings.redis_ssl,
            password=settings.redis_password,
            decode_responses=True
        )
    
    def get(self, key: str, namespace: str = "default") -> Optional[Any]:
        """Get cached value"""
        full_key = f"{namespace}:{key}"
        value = self.redis.get(full_key)
        return json.loads(value) if value else None
    
    def set(self, key: str, value: Any, ttl: int, namespace: str = "default"):
        """Set cached value with TTL (seconds)"""
        full_key = f"{namespace}:{key}"
        self.redis.setex(full_key, ttl, json.dumps(value))
    
    def delete(self, key: str, namespace: str = "default"):
        """Delete cached value"""
        full_key = f"{namespace}:{key}"
        self.redis.delete(full_key)
    
    def exists(self, key: str, namespace: str = "default") -> bool:
        """Check if key exists"""
        full_key = f"{namespace}:{key}"
        return self.redis.exists(full_key) > 0

# Singleton instances
cache = CacheStore()
