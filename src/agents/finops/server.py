from fastapi import FastAPI
from pydantic import BaseModel
from pathlib import Path
import boto3
from datetime import datetime, timedelta
from src.core.cache import cache
from src.core.bedrock import bedrock
from src.core.config import settings
from src.core.rag_client import RAGClient
import uvicorn

app = FastAPI(title="FinOps Agent Service")

class FinOpsAgent:
    def __init__(self):
        self.ce = boto3.client('ce')
        self.system_prompt = self._load_prompt()
        
        # Initialize RAG if enabled
        self.rag_client = None
        if settings.rag_enabled and settings.finops_knowledge_base_id:
            try:
                self.rag_client = RAGClient(settings.finops_knowledge_base_id)
            except Exception as e:
                print(f"RAG initialization failed: {e}")
    
    def _load_prompt(self) -> str:
        prompt_path = Path(__file__).parent / "prompt.md"
        return prompt_path.read_text()
    
    def process(self, query: str) -> str:
        cache_key = f"query:{hash(query)}"
        cached = cache.get(cache_key, namespace="finops")
        if cached:
            return cached
        
        # Get real-time cost data
        cost_data = self._get_cost_data()
        
        # Get RAG context if enabled
        rag_context = ""
        if self.rag_client:
            rag_context = self.rag_client.retrieve_and_format(query, max_results=3)
        
        # Build context with both sources
        context_parts = [f"Current Cost Data:\n{cost_data}"]
        if rag_context:
            context_parts.append(f"\nHistorical Context & Best Practices:\n{rag_context}")
        context_parts.append(f"\nUser Query: {query}")
        
        context = "\n".join(context_parts)
        
        response = bedrock.invoke(
            messages=[{"role": "user", "content": context}],
            system_prompt=self.system_prompt,
            use_cache=True
        )
        
        cache.set(cache_key, response, ttl=3600, namespace="finops")
        return response
    
    def _get_cost_data(self) -> str:
        cached = cache.get("cost_data", namespace="finops")
        if cached:
            return cached
        
        end = datetime.now().date()
        start = end - timedelta(days=30)
        
        response = self.ce.get_cost_and_usage(
            TimePeriod={'Start': start.isoformat(), 'End': end.isoformat()},
            Granularity='MONTHLY',
            Metrics=['UnblendedCost']
        )
        
        total = sum(float(r['Total']['UnblendedCost']['Amount']) 
                   for r in response['ResultsByTime'])
        
        result = f"Last 30 days cost: ${total:.2f}"
        cache.set("cost_data", result, ttl=3600, namespace="finops")
        return result

agent = FinOpsAgent()

class QueryRequest(BaseModel):
    input_text: str
    user_id: str
    session_id: str
    chat_history: list = []

class QueryResponse(BaseModel):
    response: str

@app.post("/process", response_model=QueryResponse)
async def process(request: QueryRequest):
    response = agent.process(request.input_text)
    return QueryResponse(response=response)

@app.get("/health")
async def health():
    return {"status": "healthy", "agent": "finops"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8003)
