---
name: rds-patterns
description: Use when designing database infrastructure, choosing between RDS/Aurora/Serverless, configuring HA/DR, or optimizing performance. Covers decision matrix, Multi-AZ, read replicas, Performance Insights, backup/PITR, RDS Proxy, and <org> context.
keywords: [rds, ha, dr, az, pitr]
---
# AWS RDS Patterns

Relational database patterns for <org>'s AWS environments.

## Decision matrix

| Criteria | RDS (standard) | Aurora | Aurora Serverless v2 |
|----------|---------------|--------|---------------------|
| Cost (steady) | Lowest for small DBs | ~20% more than RDS | Pay per ACU (variable) |
| Cost (variable) | Fixed (over-provisioned) | Fixed | ✅ Scales to zero-ish (0.5 ACU min) |
| HA | Multi-AZ (failover ~60s) | Built-in (6 copies, 3 AZs) | Built-in |
| Read scaling | Read replicas (async) | Up to 15 replicas (ms lag) | Auto-scales readers |
| Storage | Manual provisioning (gp3/io2) | Auto-grows (10GB→128TB) | Auto-grows |
| Failover time | ~60s (Multi-AZ) | ~30s | ~30s |
| Engine versions | All versions | Subset (Aurora-compatible) | Subset |
| Max storage | 64 TB | 128 TB | 128 TB |
| <org> preference | Legacy only | ✅ **New projects** | Serverless/batch workloads |

**<org> standard**: Aurora PostgreSQL for new projects. Aurora Serverless v2 for dev/batch with variable load.

## Aurora PostgreSQL (<org> default)

```hcl
resource "aws_rds_cluster" "main" {
  cluster_identifier = "dpm-people-prd"
  engine             = "aurora-postgresql"
  engine_version     = "15.4"
  database_name      = "people"
  master_username    = "admin"
  master_password    = var.db_password  # From Secrets Manager

  vpc_security_group_ids = [aws_security_group.db.id]
  db_subnet_group_name   = aws_db_subnet_group.main.name

  storage_encrypted = true
  kms_key_id        = var.kms_key_arn

  backup_retention_period      = 14
  preferred_backup_window      = "03:00-04:00"
  preferred_maintenance_window = "sun:04:00-sun:05:00"

  deletion_protection = true
  skip_final_snapshot = false
  final_snapshot_identifier = "dpm-people-prd-final"

  enabled_cloudwatch_logs_exports = ["postgresql"]

  tags = {
    Environment = "PRD"
    CostCenter  = "<cost-center>"
    CostScope   = "DATABASE-MANAGED"
    CostProject = "PEOPLE"
    Name        = "DPM-PEOPLE-DB-PRD"
  }
}

resource "aws_rds_cluster_instance" "writer" {
  identifier         = "dpm-people-prd-writer"
  cluster_identifier = aws_rds_cluster.main.id
  instance_class     = "db.r6g.large"  # Graviton
  engine             = aws_rds_cluster.main.engine
  engine_version     = aws_rds_cluster.main.engine_version

  performance_insights_enabled    = true
  performance_insights_kms_key_id = var.kms_key_arn
  monitoring_interval             = 60
  monitoring_role_arn             = var.enhanced_monitoring_role_arn

  tags = {
    Environment = "PRD"
    CostCenter  = "<cost-center>"
    CostScope   = "DATABASE-MANAGED"
    CostProject = "PEOPLE"
    Name        = "DPM-PEOPLE-DB-PRD-WRITER"
  }
}

resource "aws_rds_cluster_instance" "reader" {
  identifier         = "dpm-people-prd-reader-1"
  cluster_identifier = aws_rds_cluster.main.id
  instance_class     = "db.r6g.large"
  engine             = aws_rds_cluster.main.engine
  engine_version     = aws_rds_cluster.main.engine_version

  performance_insights_enabled    = true
  performance_insights_kms_key_id = var.kms_key_arn
  monitoring_interval             = 60
  monitoring_role_arn             = var.enhanced_monitoring_role_arn

  tags = {
    Environment = "PRD"
    CostCenter  = "<cost-center>"
    CostScope   = "DATABASE-MANAGED"
    CostProject = "PEOPLE"
    Name        = "DPM-PEOPLE-DB-PRD-READER-1"
  }
}
```

## Multi-AZ vs Read Replicas

| Feature | Multi-AZ | Read Replicas |
|---------|----------|---------------|
| Purpose | **High availability** | **Read scaling** |
| Failover | Automatic (~30-60s) | Manual promotion |
| Replication | Synchronous | Asynchronous (ms lag for Aurora) |
| Read traffic | ❌ Standby not readable (RDS) / ✅ Aurora readers | ✅ Readable |
| Cost | 2x instance cost | Per-replica cost |
| Cross-region | ❌ (RDS) / ✅ Aurora Global | ✅ |

**Aurora combines both**: every reader is also a failover target (automatic).

### Aurora reader endpoint

```
# Writer endpoint (read-write)
dpm-people-prd.cluster-abc123.us-east-1.rds.amazonaws.com

# Reader endpoint (load-balanced across readers)
dpm-people-prd.cluster-ro-abc123.us-east-1.rds.amazonaws.com
```

Application pattern: use writer for writes, reader endpoint for reads.

## Aurora Serverless v2

Scales ACUs (Aurora Capacity Units) based on load. 1 ACU ≈ 2 GB RAM + proportional CPU.

```hcl
resource "aws_rds_cluster" "serverless" {
  cluster_identifier = "dpm-batch-dev"
  engine             = "aurora-postgresql"
  engine_mode        = "provisioned"  # Serverless v2 uses provisioned mode
  engine_version     = "15.4"

  serverlessv2_scaling_configuration {
    min_capacity = 0.5   # Minimum (saves cost when idle)
    max_capacity = 16    # Maximum ACUs
  }

  # ... same config as above
}

resource "aws_rds_cluster_instance" "serverless" {
  identifier         = "dpm-batch-dev-1"
  cluster_identifier = aws_rds_cluster.serverless.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.serverless.engine
  engine_version     = aws_rds_cluster.serverless.engine_version
}
```

Use for: DEV environments, batch workloads (BTC), variable-traffic services.

## Performance Insights

Identifies top SQL queries, waits, and hosts consuming resources.

```hcl
resource "aws_rds_cluster_instance" "with_pi" {
  # ...
  performance_insights_enabled          = true
  performance_insights_kms_key_id       = var.kms_key_arn
  performance_insights_retention_period = 731  # 2 years (free: 7 days)
}
```

### Querying Performance Insights

```bash
# Top SQL by load (last hour)
aws pi get-resource-metrics \
  --service-type RDS \
  --identifier db-ABC123 \
  --metric-queries '[{"Metric": "db.load.avg", "GroupBy": {"Group": "db.sql", "Limit": 10}}]' \
  --start-time $(date -d '1 hour ago' -u +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period-in-seconds 60
```

### Enhanced Monitoring

OS-level metrics (CPU per process, memory, disk I/O) at 1-60s granularity.

```hcl
resource "aws_rds_cluster_instance" "monitored" {
  # ...
  monitoring_interval = 60  # seconds (1, 5, 10, 15, 30, 60)
  monitoring_role_arn = aws_iam_role.rds_monitoring.arn
}

resource "aws_iam_role" "rds_monitoring" {
  name = "rds-enhanced-monitoring"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "monitoring.rds.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  role       = aws_iam_role.rds_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}
```

### Slow query logs

```hcl
resource "aws_rds_cluster_parameter_group" "postgres" {
  family = "aurora-postgresql15"
  name   = "dpm-people-prd-params"

  parameter {
    name  = "log_min_duration_statement"
    value = "1000"  # Log queries >1s
  }

  parameter {
    name  = "log_statement"
    value = "ddl"  # Log DDL statements
  }

  parameter {
    name  = "shared_preload_libraries"
    value = "pg_stat_statements"
  }
}
```

## Backup and DR

### Automated backups + PITR

Aurora: continuous backup to S3 (automatic). PITR granularity: 5 minutes.

```hcl
resource "aws_rds_cluster" "with_backup" {
  # ...
  backup_retention_period = 14  # days (<org> minimum for PRD)
  preferred_backup_window = "03:00-04:00"  # UTC, off-peak
}
```

### Cross-region snapshot copy (DR)

```bash
# Copy latest snapshot to another region
aws rds copy-db-cluster-snapshot \
  --source-db-cluster-snapshot-identifier arn:aws:rds:us-east-1:<ACCOUNT_ID>:cluster-snapshot:dpm-people-prd-2026-05-28 \
  --target-db-cluster-snapshot-identifier dpm-people-prd-dr-copy \
  --region us-west-2 \
  --kms-key-id arn:aws:kms:us-west-2:<ACCOUNT_ID>:key/dr-key-id
```

### Retention recommendations

| Environment | Retention | PITR | Cross-region |
|-------------|-----------|------|--------------|
| PRD | 14-35 days | ✅ Always | ✅ Weekly |
| BTC | 7-14 days | ✅ | Optional |
| HML | 7 days | ✅ | ❌ |
| DEV | 1-7 days | Optional | ❌ |

## Parameter groups

Never use the `default` parameter group — it can't be modified.

```hcl
resource "aws_rds_cluster_parameter_group" "custom" {
  family = "aurora-postgresql15"
  name   = "dpm-people-prd-cluster-params"

  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }

  parameter {
    name  = "idle_in_transaction_session_timeout"
    value = "60000"  # 60s — kill idle transactions
  }

  parameter {
    name  = "statement_timeout"
    value = "300000"  # 5min max query time
  }

  tags = {
    Environment = "PRD"
    CostCenter  = "<cost-center>"
    CostScope   = "DATABASE-MANAGED"
    CostProject = "PEOPLE"
    Name        = "DPM-PEOPLE-PARAMS-PRD"
  }
}
```

## Encryption at rest

All <org> databases MUST be encrypted with KMS.

```hcl
resource "aws_rds_cluster" "encrypted" {
  # ...
  storage_encrypted = true
  kms_key_id        = aws_kms_key.rds.arn  # Customer-managed key
}
```

Cannot enable encryption on existing unencrypted cluster — must snapshot + restore with encryption.

## RDS Proxy

Connection pooling for serverless/Lambda workloads that open many short-lived connections.

```hcl
resource "aws_db_proxy" "main" {
  name                   = "dpm-people-proxy"
  debug_logging          = false
  engine_family          = "POSTGRESQL"
  idle_client_timeout    = 1800
  require_tls            = true
  role_arn               = aws_iam_role.proxy.arn
  vpc_security_group_ids = [aws_security_group.proxy.id]
  vpc_subnet_ids         = var.private_subnet_ids

  auth {
    auth_scheme = "SECRETS"
    iam_auth    = "REQUIRED"
    secret_arn  = aws_secretsmanager_secret.db_creds.arn
  }

  tags = {
    Environment = "PRD"
    CostCenter  = "<cost-center>"
    CostScope   = "DATABASE-MANAGED"
    CostProject = "PEOPLE"
    Name        = "DPM-PEOPLE-PROXY-PRD"
  }
}

resource "aws_db_proxy_default_target_group" "main" {
  db_proxy_name = aws_db_proxy.main.name

  connection_pool_config {
    max_connections_percent      = 100
    max_idle_connections_percent = 50
    connection_borrow_timeout    = 120
  }
}

resource "aws_db_proxy_target" "main" {
  db_proxy_name         = aws_db_proxy.main.name
  target_group_name     = "default"
  db_cluster_identifier = aws_rds_cluster.main.id
}
```

Use RDS Proxy when:
- Lambda functions connect to RDS (connection storms)
- Many microservices share one DB (connection pooling)
- Need IAM-based DB authentication

## <org>-specific patterns

### Instance class selection

| Workload | Recommended class | Notes |
|----------|------------------|-------|
| PRD (steady) | `db.r6g.large`+ | Graviton, memory-optimized |
| PRD (high IOPS) | `db.r6g.xlarge`+ | More network bandwidth |
| DEV/HML | `db.t4g.medium` | Burstable, Graviton |
| Batch (BTC) | Aurora Serverless v2 | Scales with batch load |

**Always use Graviton** (`r6g`, `t4g`) — 20% cheaper than Intel equivalents.

### Tagging

```hcl
tags = {
  Environment = "PRD"
  CostCenter  = "<cost-center>"  # or relevant team
  CostScope   = "DATABASE-MANAGED"
  CostProject = "PEOPLE"
  Name        = "DPM-PEOPLE-DB-PRD"
}
```

### Connection from EKS pods

Pods connect via ExternalSecret-synced credentials:
```yaml
# ExternalSecret → K8s Secret → Pod env var
env:
  - name: DATABASE_URL
    valueFrom:
      secretKeyRef:
        name: dpm-people-db-creds
        key: connection-string
```

Connection string format:
```
postgresql://user:pass@dpm-people-prd.cluster-abc123.us-east-1.rds.amazonaws.com:5432/people?sslmode=require
```

### Monitoring integration

RDS metrics flow to CloudWatch. For <org>'s VictoriaMetrics stack, use CloudWatch exporter or vmagent `cloudwatch_sd_configs`:
```yaml
# vmagent scrape config for RDS metrics
- job_name: rds
  cloudwatch_sd_configs:
    - region: us-east-1
      namespace: AWS/RDS
      metrics:
        - name: CPUUtilization
        - name: DatabaseConnections
        - name: FreeableMemory
        - name: ReadIOPS
        - name: WriteIOPS
```

## Useful commands

```bash
# List clusters
aws rds describe-db-clusters --query "DBClusters[*].[DBClusterIdentifier,Status,Engine]" --output table

# Check cluster status
aws rds describe-db-clusters --db-cluster-identifier dpm-people-prd

# Failover (test)
aws rds failover-db-cluster --db-cluster-identifier dpm-people-prd

# Create manual snapshot
aws rds create-db-cluster-snapshot \
  --db-cluster-identifier dpm-people-prd \
  --db-cluster-snapshot-identifier dpm-people-prd-manual-$(date +%Y%m%d)

# Restore from PITR
aws rds restore-db-cluster-to-point-in-time \
  --source-db-cluster-identifier dpm-people-prd \
  --db-cluster-identifier dpm-people-prd-restored \
  --restore-to-time 2026-05-28T12:00:00Z
```

## Anti-patterns

- ❌ **db.m5.large default without analysis** — always benchmark; often `db.r6g.medium` (Graviton) is better and cheaper
- ❌ **Single-AZ in production** — Aurora is multi-AZ by default, but RDS standard requires explicit Multi-AZ config
- ❌ **Backup retention 1 day** — minimum 14 days for PRD at <org>; 1 day means you can't recover from yesterday's corruption
- ❌ **Default parameter group** — can't be modified; always create custom parameter group
- ❌ **No encryption** — all <org> databases must be encrypted (KMS); can't enable later without snapshot+restore
- ❌ **Public accessibility** — databases MUST be in private subnets only; access via VPC or bastion
- ❌ **No Performance Insights** — free for 7 days retention; no reason to skip it
- ❌ **Connection per request** — use connection pooling (PgBouncer, RDS Proxy, or app-level pool)
- ❌ **No idle transaction timeout** — idle transactions hold locks; set `idle_in_transaction_session_timeout`
- ❌ **Intel instances when Graviton available** — `db.r6g` is 20% cheaper than `db.r6i` with same performance
- ❌ **No deletion protection** — always enable `deletion_protection = true` for PRD
- ❌ **Skipping final snapshot** — `skip_final_snapshot = true` in PRD means data loss on accidental delete

## Reference

- Aurora docs: https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/
- RDS Proxy: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/rds-proxy.html
- Performance Insights: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PerfInsights.html
- Related: `iam-patterns`, `cost-explorer`, `external-secrets-aws-sm`
