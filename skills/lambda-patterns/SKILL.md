---
name: lambda-patterns
description: "Use when designing Lambda functions, optimizing cold starts, configuring VPC access, or integrating observability. Covers cold start mitigation, layers, memory/CPU tuning, OTel integration, execution roles, and <org> context."
keywords: [lambda, otel, vpc, cpu]
---
# AWS Lambda Patterns

Serverless compute patterns for <org>'s AWS environments.

## When to use Lambda at <org>

| Use case | Lambda fit | Alternative |
|----------|-----------|-------------|
| Event-driven processing (S3, SQS, DynamoDB Streams) | ✅ Excellent | — |
| Short-lived API endpoints (<15min) | ✅ Good | EKS (if always-on traffic) |
| Scheduled tasks (<15min) | ✅ Good | Argo CronWorkflow (if >15min or complex) |
| Long-running processes | ❌ Bad | EKS pods, Step Functions |
| High-throughput steady-state APIs | ❌ Bad | EKS with KEDA autoscaling |
| Stateful workloads | ❌ Bad | EKS with PVC |

<org> preference: **EKS for most workloads** (standardized observability, GitOps, Helm charts). Lambda for event-driven glue and lightweight automation.

## Cold start mitigation

Cold start = time to initialize runtime + download code + run init code. Affects latency-sensitive workloads.

### Factors affecting cold start

| Factor | Impact | Mitigation |
|--------|--------|-----------|
| Runtime | Java/C# > Python/Node > Go/Rust | Choose lighter runtime or use SnapStart |
| Package size | Larger = slower download | Minimize deps, use layers |
| VPC | +2-8s (ENI creation) | Use VPC only when needed |
| Memory | More memory = faster init | Tune memory (see below) |
| Init code | Heavy constructors, DB connections | Lazy init, connection pooling |

### Provisioned Concurrency

Pre-warms N instances. Eliminates cold starts but costs money even when idle.

```hcl
resource "aws_lambda_provisioned_concurrency_config" "api" {
  function_name                  = aws_lambda_function.api.function_name
  provisioned_concurrent_executions = 5
  qualifier                      = aws_lambda_alias.live.name
}
```

Use for: latency-critical APIs, customer-facing endpoints.

### SnapStart (Java/.NET only)

Snapshots the initialized function. Restores from snapshot instead of cold-starting.

```hcl
resource "aws_lambda_function" "java_api" {
  function_name = "my-java-api"
  runtime       = "java21"
  handler       = "com.bdc.Handler::handleRequest"

  snap_start {
    apply_on = "PublishedVersions"
  }

  # ...
}
```

Caveats:
- Only works with published versions (not `$LATEST`)
- Uniqueness: don't cache random seeds or connection IDs in init
- Not available for all runtimes

## Lambda Layers

Shared dependencies packaged separately from function code. Reduces deployment size and enables reuse.

```hcl
resource "aws_lambda_layer_version" "otel" {
  layer_name          = "otel-python-layer"
  compatible_runtimes = ["python3.11"]
  filename            = "otel-layer.zip"
}

resource "aws_lambda_function" "processor" {
  # ...
  layers = [
    aws_lambda_layer_version.otel.arn,
    "arn:aws:lambda:us-east-1:901920570463:layer:aws-otel-python-amd64-ver-1-25-0:1"
  ]
}
```

Common layers at <org>:
- AWS OTel Lambda layer (official)
- AWS Lambda Powertools (Python/TypeScript)
- Shared business logic (internal)

## Memory tuning = CPU tuning

Lambda allocates CPU proportional to memory. More memory = more CPU = faster execution.

| Memory | vCPU equivalent | Use for |
|--------|----------------|---------|
| 128 MB | ~0.08 vCPU | Minimal (tiny transforms) |
| 512 MB | ~0.33 vCPU | Light processing |
| 1024 MB | ~0.58 vCPU | Standard APIs |
| 1769 MB | 1 vCPU | CPU-intensive |
| 3538 MB | 2 vCPU | Heavy compute |
| 10240 MB | 6 vCPU | Max (ML inference, video) |

### Power Tuning

Use AWS Lambda Power Tuning (Step Functions-based) to find optimal memory:

```bash
# Deploy power tuning tool
aws serverlessrepo create-cloud-formation-change-set \
  --application-id arn:aws:serverlessrepo:us-east-1:451282441545:applications/aws-lambda-power-tuning \
  --stack-name lambda-power-tuning
```

Often, increasing memory from 128→512 MB reduces duration enough to **lower total cost** (billed per GB-ms).

## VPC configuration

Lambda in VPC can access private resources (RDS, ElastiCache, internal APIs) but adds cold start latency.

```hcl
resource "aws_lambda_function" "vpc_function" {
  # ...

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }
}
```

### VPC cold start (ENI provisioning)

AWS improved this significantly (Hyperplane ENI). Current impact: ~1-2s additional cold start (down from 8-10s historically).

### When to use VPC

- ✅ Need to access RDS/ElastiCache in private subnets
- ✅ Need to call internal APIs (EKS services via NLB)
- ❌ Only calling public AWS APIs (use VPC endpoints or no VPC)
- ❌ Only calling external APIs (no VPC needed)

### Internet access from VPC Lambda

Lambda in VPC has NO internet access by default. Options:
1. NAT Gateway (costs money)
2. VPC Endpoints for AWS services (cheaper for high traffic)

## Observability

### AWS Lambda Powertools (recommended for Lambda-native)

```python
from aws_lambda_powertools import Logger, Tracer, Metrics
from aws_lambda_powertools.metrics import MetricUnit

logger = Logger()
tracer = Tracer()
metrics = Metrics()

@logger.inject_lambda_context
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event, context):
    logger.info("Processing event", extra={"event_type": event.get("type")})
    metrics.add_metric(name="EventsProcessed", unit=MetricUnit.Count, value=1)
    return {"statusCode": 200}
```

### OTel via Collector (<org> preferred for cross-service traces)

For Lambda functions that participate in distributed traces with EKS services, use the OTel Lambda layer:

```hcl
resource "aws_lambda_function" "traced" {
  # ...
  layers = [
    "arn:aws:lambda:us-east-1:901920570463:layer:aws-otel-python-amd64-ver-1-25-0:1"
  ]

  environment {
    variables = {
      AWS_LAMBDA_EXEC_WRAPPER            = "/opt/otel-instrument"
      OTEL_SERVICE_NAME                  = "my-lambda-processor"
      OTEL_EXPORTER_OTLP_ENDPOINT        = "https://otelcollector-prd.<old-internal-domain>:443"
      OTEL_PROPAGATORS                   = "tracecontext,baggage"
      OPENTELEMETRY_COLLECTOR_CONFIG_FILE = "/var/task/collector.yaml"
    }
  }
}
```

### X-Ray vs OTel

| Aspect | X-Ray | OTel via Collector |
|--------|-------|-------------------|
| Setup | Built-in, zero config | Layer + env vars |
| Cross-service (EKS) | Limited | ✅ Full correlation |
| Backend | AWS X-Ray console | Tempo (<org> standard) |
| Cost | Free tier generous | Collector infra cost |
| <org> recommendation | Quick debugging | **Production standard** |

**<org> standard**: OTel via Collector for production Lambda functions that interact with EKS services. X-Ray acceptable for isolated automation Lambda.

## IAM — Execution roles

Lambda execution role = what the function can do. Equivalent to IRSA for EKS pods.

```hcl
resource "aws_iam_role" "lambda_exec" {
  name = "lambda-processor-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = {
    Environment = "PRD"
    CostCenter  = "<cost-center>"
    CostScope   = "PROCESS"
    CostProject = "EVENT-PROCESSOR"
    Name        = "lambda-processor-role"
  }
}

# Minimum: CloudWatch Logs
resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# VPC access (if in VPC)
resource "aws_iam_role_policy_attachment" "vpc" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# Custom permissions (least privilege)
resource "aws_iam_role_policy" "custom" {
  role = aws_iam_role.lambda_exec.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:PutObject"]
      Resource = "arn:aws:s3:::my-bucket/processed/*"
    }]
  })
}
```

### Resource-based policies

Allow other services to invoke the Lambda:

```hcl
resource "aws_lambda_permission" "s3_trigger" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.processor.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.uploads.arn
  source_account = "<ACCOUNT_ID>"
}
```

## Event source patterns

### SQS trigger (most common at <org>)

```hcl
resource "aws_lambda_event_source_mapping" "sqs" {
  event_source_arn                   = aws_sqs_queue.events.arn
  function_name                      = aws_lambda_function.processor.arn
  batch_size                         = 10
  maximum_batching_window_in_seconds = 5
  function_response_types            = ["ReportBatchItemFailures"]
}
```

### S3 event trigger

```hcl
resource "aws_s3_bucket_notification" "upload" {
  bucket = aws_s3_bucket.uploads.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.processor.arn
    events             = ["s3:ObjectCreated:*"]
    filter_prefix      = "incoming/"
  }
}
```

## <org>-specific patterns

### Tagging

```hcl
tags = {
  Environment = "PRD"
  CostCenter  = "<cost-center>"
  CostScope   = "PROCESS"
  CostProject = "EVENT-PROCESSOR"
  Name        = "DPM-EVENT-PROCESSOR-PRD"
}
```

### Lambda vs EKS decision at <org>

<org> defaults to EKS for most workloads. Use Lambda when:
1. Event-driven with unpredictable traffic (S3 uploads, SQS)
2. Short execution (<5 min typical)
3. No need for persistent connections or state
4. Automation/glue (CloudWatch Events, Config rules)

### Cross-service tracing

Lambda → EKS service: propagate `traceparent` header in HTTP calls:
```python
import requests
from opentelemetry.propagate import inject

headers = {}
inject(headers)
response = requests.get("https://api.<org-domain>/v1/data", headers=headers)
```

## Useful commands

```bash
# Invoke function
aws lambda invoke --function-name my-func --payload '{"key":"value"}' output.json

# View recent logs
aws logs tail /aws/lambda/my-func --since 1h --follow

# Check concurrency
aws lambda get-function-concurrency --function-name my-func

# List layers
aws lambda list-layers --compatible-runtime python3.11

# Update function config
aws lambda update-function-configuration \
  --function-name my-func \
  --memory-size 1024 \
  --timeout 30
```

## Anti-patterns

- ❌ **Monolithic Lambda** — single function doing everything; split by responsibility
- ❌ **Synchronous invocation cascades** — Lambda A calls Lambda B calls Lambda C synchronously; use Step Functions or async (SQS)
- ❌ **VPC without need** — adds cold start latency; only use for private resource access
- ❌ **128 MB default** — almost always too low; benchmark with Power Tuning
- ❌ **No dead-letter queue** — failed events disappear; always configure DLQ on async invocations
- ❌ **Hardcoded endpoints** — use environment variables for all URLs/ARNs
- ❌ **Fat deployment packages** — >50 MB slows cold start; use layers for shared deps
- ❌ **No timeout configured** — default 3s may be too short; set explicit timeout based on expected duration
- ❌ **Wildcard IAM permissions** — `"Action": "*"` on execution role; use least privilege (see `iam-patterns`)
- ❌ **X-Ray only for cross-service** — X-Ray traces don't correlate with EKS OTel traces in Tempo; use OTel layer
- ❌ **Connection per invocation** — open DB/Redis connections in init (outside handler), reuse across invocations
- ❌ **Ignoring partial batch failures** — use `ReportBatchItemFailures` for SQS to avoid reprocessing entire batch

## Reference

- Lambda docs: https://docs.aws.amazon.com/lambda/
- Lambda Power Tuning: https://github.com/alexcasalboni/aws-lambda-power-tuning
- Lambda Powertools: https://docs.powertools.aws.dev/lambda/python/latest/
- OTel Lambda layer: https://aws-otel.github.io/docs/getting-started/lambda
- Related: `iam-patterns`, `cost-explorer`, `<org>-telemetry-standard`
