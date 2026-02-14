import boto3
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional
import time
from opentelemetry import trace
from src.core.agent_base import Agent
from src.core.state_store import ConversationMessage
from src.core.cache import cache
from src.core.bedrock import bedrock
from src.core.config import settings
from src.core.logger import logger, log_request, log_response, log_error

tracer = trace.get_tracer(__name__)

class FinOpsAgent(Agent):
    """FinOps/Cost specialist agent with Kubecost integration"""
    
    def __init__(self):
        super().__init__(
            agent_id="finops",
            name="FinOps Agent",
            description="Specializes in cloud costs and financial optimization"
        )
        self.ce = boto3.client('ce')
        self.athena = boto3.client('athena', region_name=settings.athena_region)
        self.system_prompt = self._load_prompt()
    
    async def process_request(
        self,
        input_text: str,
        user_id: str,
        session_id: str,
        chat_history: List[ConversationMessage],
        additional_params: Optional[dict] = None
    ) -> ConversationMessage:
        """Process FinOps-related query with conversation history"""
        
        start_time = time.time()
        
        with tracer.start_as_current_span("finops_agent.process_request") as span:
            span.set_attribute("agent_id", self.id)
            span.set_attribute("user_id", user_id)
            span.set_attribute("session_id", session_id)
            
            log_request(self.id, user_id, session_id, input_text)
            
            try:
                if not input_text or not input_text.strip():
                    raise ValueError("Input text cannot be empty")
                
                if len(input_text) > 10000:
                    raise ValueError("Input text too long (max 10000 characters)")
                
                cache_key = f"query:{hash(input_text)}"
                cached = cache.get(cache_key, namespace="finops")
                if cached:
                    logger.info("Cache hit", extra={"agent_id": self.id, "cache_key": cache_key})
                    return ConversationMessage(
                        role="assistant",
                        content=cached,
                        timestamp=datetime.utcnow().isoformat(),
                        agent_id=self.id
                    )
                
                with tracer.start_as_current_span("finops_agent.get_costs"):
                    aws_costs = self._get_aws_costs()
                    k8s_costs = self._get_kubecost_data()
                
                history_context = self._format_history(chat_history)
                context = f"""AWS Costs:
{aws_costs}

Kubernetes Costs (Kubecost):
{k8s_costs}

Conversation History:
{history_context}

Current Query: {input_text}"""
                
                with tracer.start_as_current_span("finops_agent.bedrock_invoke"):
                    response = bedrock.invoke(
                        messages=[{"role": "user", "content": context}],
                        system_prompt=self.system_prompt,
                        use_cache=True
                    )
                
                cache.set(cache_key, response, ttl=3600, namespace="finops")
                
                duration_ms = (time.time() - start_time) * 1000
                log_response(self.id, user_id, session_id, len(response), duration_ms)
                
                return ConversationMessage(
                    role="assistant",
                    content=response,
                    timestamp=datetime.utcnow().isoformat(),
                    agent_id=self.id
                )
                
            except Exception as e:
                log_error(self.id, e, user_id=user_id, session_id=session_id)
                raise
    
    def _get_aws_costs(self) -> str:
        cached = cache.get("aws_costs", namespace="finops")
        if cached:
            return cached
        
        end = datetime.now().date()
        start = end - timedelta(days=30)
        
        try:
            response = self.ce.get_cost_and_usage(
                TimePeriod={'Start': start.isoformat(), 'End': end.isoformat()},
                Granularity='MONTHLY',
                Metrics=['UnblendedCost']
            )
            
            total = sum(float(r['Total']['UnblendedCost']['Amount']) 
                       for r in response['ResultsByTime'])
            
            result = f"Last 30 days AWS cost: ${total:.2f}"
        except Exception as e:
            logger.error("Error fetching AWS costs", extra={"error": str(e)})
            result = f"Error fetching AWS costs: {e}"
        
        cache.set("aws_costs", result, ttl=3600, namespace="finops")
        return result
    
    def _get_kubecost_data(self) -> str:
        cached = cache.get("kubecost_data", namespace="finops")
        if cached:
            return cached
        
        query = f"""
        SELECT 
            namespace,
            SUM(cost) as total_cost,
            AVG(cpu_allocation) as avg_cpu,
            AVG(memory_allocation) as avg_memory
        FROM {settings.athena_database}.{settings.athena_table}
        WHERE date >= current_date - interval '30' day
        GROUP BY namespace
        ORDER BY total_cost DESC
        LIMIT 10
        """
        
        try:
            result = self._execute_athena_query(query)
        except Exception as e:
            logger.error("Error fetching Kubecost data", extra={"error": str(e)})
            result = f"Error fetching Kubecost data: {e}"
        
        cache.set("kubecost_data", result, ttl=3600, namespace="finops")
        return result
    
    def _execute_athena_query(self, query: str) -> str:
        response = self.athena.start_query_execution(
            QueryString=query,
            QueryExecutionContext={'Database': settings.athena_database},
            ResultConfiguration={'OutputLocation': settings.athena_bucket},
            WorkGroup=settings.athena_workgroup
        )
        
        query_execution_id = response['QueryExecutionId']
        
        max_attempts = 30
        for _ in range(max_attempts):
            status = self.athena.get_query_execution(QueryExecutionId=query_execution_id)
            state = status['QueryExecution']['Status']['State']
            
            if state == 'SUCCEEDED':
                break
            elif state in ['FAILED', 'CANCELLED']:
                reason = status['QueryExecution']['Status'].get('StateChangeReason', 'Unknown')
                return f"Query failed: {reason}"
            
            time.sleep(1)
        
        results = self.athena.get_query_results(QueryExecutionId=query_execution_id)
        
        rows = results['ResultSet']['Rows']
        if len(rows) <= 1:
            return "No data found"
        
        data_rows = rows[1:]
        formatted = []
        for row in data_rows[:10]:
            values = [col.get('VarCharValue', 'N/A') for col in row['Data']]
            formatted.append(' | '.join(values))
        
        return '\n'.join(formatted)
    
    def _format_history(self, messages: List[ConversationMessage]) -> str:
        if not messages:
            return "No previous conversation"
        return "\n".join([f"{msg.role}: {msg.content}" for msg in messages[-5:]])
