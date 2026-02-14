# Arquitetura

## Overview

```
+-------------------------------------------------------------+
|                         SLACK                               |
+--------------------+----------------------------------------+
                     ?
+-------------------------------------------------------------+
|                    Ingress (ALB)                            |
+--------------------+----------------------------------------+
                     ?
+-------------------------------------------------------------+
|              Supervisor (LangGraph)                         |
|  - Analisa request                                          |
|  - Delega for especialistas                                |
|  - Consolida respostas                                      |
+--------------------+----------------------------------------+
                     ?
         +-----------+-----------+
         ?           ?           ?
+------------+ +------------+ +------------+
| AWS Agent  | | K8s Agent  | |FinOps Agent|
| (3-15 pods)| | (2-10 pods)| | (1-5 pods) |
+------------+ +------------+ +------------+
         ?           ?           ?
+------------+ +--------------------------+
|DevOps Agent| | Observability Agent      |
| (1-5 pods) | | (2-8 pods)               |
+------------+ +--------------------------+
         ?
+-------------------------------------------------------------+
|                    Data Layer                               |
|  +----------------------+  +----------------------+        |
|  | DynamoDB             |  | ElastiCache Redis    |        |
|  | - Session state      |  | - Inventory cache    |        |
|  | - TTL: 24h           |  | - TTL: 1-60min       |        |
|  +----------------------+  +----------------------+        |
+-------------------------------------------------------------+
```

## Componentes

### Supervisor
- **Function**: Orchestrates specialists
- **Tech**: LangGraph + Bedrock (Claude 3.5 Sonnet)
- **State**: DynamoDB
- **Scale**: 2-10 pods (HPA)

### Especialistas
Each um roda em pod sefordo com escala independente:

| Agent | Function | Scale | Cache TTL |
|-------|--------|--------|-----------|
| AWS | Recursos AWS | 3-15 | 5min |
| Kubernetes | Cluster K8s | 2-10 | 1min |
| FinOps | Custos (AWS + Kubecost) | 1-5 | 1h |
| DevOps | CI/CD + Docs | 1-5 | 5min |
| Observability | Metrics + Anomalias | 2-8 | 1min |

### Data Layer

**DynamoDB**
- Conversational state
- Session history
- TTL automatic (24h)

**ElastiCache Redis**
- Cache de inventories
- Cache de metrics
- Namespaces isolados por agent

## Fluxo de Dados

```
1. User: @Agent Squad which EC2 are running?
   ?
2. Slack -> Supervisor
   ?
3. Supervisor analisa com Bedrock
   -> Decide: "aws" agent
   ?
4. Supervisor -> AWS Agent (HTTP)
   ?
5. AWS Agent:
   - Check cache (hit? return)
   - Query AWS API
   - Cache result (5min)
   - Return to Supervisor
   ?
6. Supervisor consolida resposta
   ?
7. Supervisor -> Slack (thread)
```

## Seguranca

### Read-Only Policy (4 camadas)
1. **System Prompts**: Instrucoes explicitas
2. **IAM**: Explicit Deny em writes
3. **K8s RBAC**: Apenas get/list/watch
4. **Responif Templates**: Recusa modificacoes

### Network
- Pods em VPC privada
- ElastiCache em subnet privada
- Ingress apenas HTTPS

## Custos

| Componente | Custo/mes |
|------------|-----------|
| DynamoDB | $5-15 |
| ElastiCache | $20-40 |
| Bedrock | $5-15 |
| EKS (incremental) | $10-30 |
| **Total** | **$40-100** |

## Scalebilidade

- **HPA**: Auto-scale baseado em CPU/Memory
- **Cache**: Reduz latencia e cost de APIs
- **Microservices**: Scale independente por agent
