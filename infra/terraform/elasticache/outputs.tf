output "cache_endpoint" {
  description = "Primary endpoint address (set as REDIS_HOST in the app)"
  value       = aws_elasticache_replication_group.this.primary_endpoint_address
}

output "cache_port" {
  description = "Cache port (set as REDIS_PORT in the app)"
  value       = aws_elasticache_replication_group.this.port
}

output "security_group_id" {
  description = "Security group protecting the cache"
  value       = aws_security_group.this.id
}
