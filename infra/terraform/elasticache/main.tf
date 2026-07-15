terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

locals {
  base_tags = merge(var.tags, {
    Module    = "elasticache"
    ManagedBy = "terraform"
    Component = var.name_prefix
  })
}

# =====================================================================
# ElastiCache (Valkey) — cache + rate/budget counters for the squad.
#
# Consumed by src/core/cache.py (datasource cache, spec 30) and
# src/core/rate_limiter.py (global rate + atomic budget, spec 31 L3/T19d).
# The app reads REDIS_HOST/REDIS_PORT/REDIS_SSL/REDIS_PASSWORD — Valkey speaks
# the Redis protocol, so redis-py connects unchanged.
#
# Minimal footprint (single node, t4g.micro, no replica) — enough to validate
# behavior against a managed cache. Scale to a Multi-AZ replication group with
# automatic failover for production (see var.node_type / a future HA phase).
#
# Engine "valkey": the open-source Redis fork; ~20-33% cheaper on ElastiCache
# and protocol-compatible with the existing client.
# =====================================================================

# Subnet group — the cache lives in the cluster's private subnets.
resource "aws_elasticache_subnet_group" "this" {
  name       = "${var.name_prefix}-cache"
  subnet_ids = var.subnet_ids
  tags       = local.base_tags
}

# Security group — only the EKS workers may reach the cache port.
resource "aws_security_group" "this" {
  name        = "${var.name_prefix}-cache"
  description = "Allow EKS workers to reach the ${var.name_prefix} Valkey cache"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Valkey/Redis from EKS workers"
    from_port       = var.port
    to_port         = var.port
    protocol        = "tcp"
    security_groups = [var.eks_worker_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.base_tags, { Name = "${var.name_prefix}-cache" })
}

# Single-node Valkey (1 node group, 0 replicas, single-AZ) — the minimal
# footprint. Valkey node-based deployments use aws_elasticache_replication_group
# with engine="valkey" (NOT aws_elasticache_cluster, which is redis/memcached
# only). Scale to Multi-AZ by raising replicas_per_node_group + enabling
# automatic_failover/multi_az in a later HA phase.
#
# SECURITY (deferred phase — see variables): in-transit/at-rest encryption and
# an AUTH token are OFF by default here so the module `validate`s and a minimal
# cluster stands up cheaply. Enable them together with an ExternalSecret
# carrying REDIS_PASSWORD (auth token) + KMS before any real use.
resource "aws_elasticache_replication_group" "this" {
  replication_group_id = "${var.name_prefix}-cache"
  description          = "${var.name_prefix} Valkey cache (spec 30/31)"

  engine         = "valkey"
  engine_version = var.engine_version
  node_type      = var.node_type
  port           = var.port

  # Minimal: one shard, no replica, no failover.
  num_node_groups            = 1
  replicas_per_node_group    = 0
  automatic_failover_enabled = false
  multi_az_enabled           = false

  parameter_group_name = var.parameter_group_name
  subnet_group_name    = aws_elasticache_subnet_group.this.name
  security_group_ids   = [aws_security_group.this.id]

  # --- Security phase (default OFF; enable with an auth-token secret + KMS) ---
  transit_encryption_enabled = var.transit_encryption_enabled

  tags = merge(local.base_tags, { Name = "${var.name_prefix}-cache" })
}
