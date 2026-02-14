# RAG Implementation - FinOps Agent

## Overview

The FinOps Agent now supports **RAG (Retrieval-Augmented Generation)** using **Amazon Bedrock Knowledge Base**.

## Architecture

```
User Query
    ?
FinOps Agent
    ?
    +--> Cost Explorer API (real-time data)
    +--> Bedrock Knowledge Base (historical context/docs)
    +--> Claude 3.5 Sonnet (analysis + response)
```

## How It Works

1. **Real-time query**: Cost Explorer API returns current costs
2. **Retrieval**: Searches relevant documents in Knowledge Base (embeddings)
3. **Augmentation**: Combines real data + historical context
4. **Generation**: Claude generates response with both sources

## Setup

### 1. Create Knowledge Base in Bedrock

```bash
# Via AWS Console
1. Access Bedrock -> Knowledge Bases
2. Create Knowledge Base
3. Name: "finops-cost-reports"
4. Data Source: S3 bucket with cost reports
5. Embeddings: Titan Embeddings G1
6. Vector Store: OpenSearch Serverless (or Pinecone)
7. Copy the Knowledge Base ID
```

### 2. Configure Variables

```bash
# .env.local
RAG_ENABLED=true
FINOPS_KNOWLEDGE_BASE_ID=ABCD1234EFGH  # Your KB ID
```

### 3. IAM Permissions

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:Retrieve",
        "bedrock:RetrieveAndGenerate"
      ],
      "Resource": "arn:aws:bedrock:*:*:knowledge-base/*"
    }
  ]
}
```

### 4. Rebuild and Restart

```bash
docker compose build finops-agent
docker compose restart finops-agent
```

## Document Types to Index

### Historical Cost Reports
- `cost-reports/2024-01.json` - Monthly costs
- `cost-reports/2024-02.json`
- `cost-reports/anomalies/spike-2024-01-15.md` - Anomaly analyses

### Best Practices
- `best-practices/reserved-instances.md`
- `best-practices/savings-plans.md`
- `best-practices/tagging-strategy.md`

### Runbooks
- `runbooks/cost-spike-investigation.md`
- `runbooks/budget-alert-response.md`

### Policies
- `policies/cost-allocation.md`
- `policies/budget-approval.md`

## Usage Example

### Without RAG (real data only)
```
User: "What is the total cost this month?"
Response: "$138,099.99 in the last 30 days"
```

### With RAG (real data + context)
```
User: "What is the total cost this month?"
Response: "$138,099.99 in the last 30 days.

Compared to history:
- January: $125k (10% increase)
- December: $142k (3% decrease)

Main drivers of increase:
- EC2: +$8k (new m5.xlarge instances)
- RDS: +$3k (upgrade to Multi-AZ)

Recommendations based on best practices:
- Consider Reserved Instances for EC2 (30% savings)
- Review RDS Multi-AZ if not for production"
```

## Benefits

? **Historical Context**: Compares current costs with previous months  
? **Best Practices**: Recommendations based on internal documentation  
? **Anomaly Analysis**: References similar past investigations  
? **Runbooks**: Suggests documented procedures  
? **Compliance**: Cites company cost policies  

## Monitoring

```python
# Logs show retrieval
{
  "message": "RAG retrieval",
  "query": "total cost",
  "results_count": 3,
  "top_score": 0.87
}
```

## Troubleshooting

### RAG not working
```bash
# Check if KB ID is correct
aws bedrock-agent get-knowledge-base --knowledge-base-id $FINOPS_KNOWLEDGE_BASE_ID

# Check permissions
aws sts get-caller-identity

# View logs
docker logs code-finops-agent-1 | grep RAG
```

### Responses without historical context
- Verify that `RAG_ENABLED=true`
- Confirm that Knowledge Base has indexed documents
- Increase `max_results` in code (default: 3)

## Costs

- **Embeddings**: ~$0.0001 per 1k tokens
- **Vector Store**: OpenSearch Serverless ~$700/month (or Pinecone ~$70/month)
- **Retrieval**: ~$0.0001 per query

**Estimate**: ~$50-100/month for moderate usage

## Next Steps

- [ ] Add RAG to DevOps Agent (GitLab documentation)
- [ ] Add RAG to Supervisor (conversation history)
- [ ] Implement feedback loop (thumbs up/down)
- [ ] A/B testing (with vs without RAG)
