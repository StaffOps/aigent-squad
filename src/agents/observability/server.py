from fastapi import FastAPI
from pydantic import BaseModel
from pathlib import Path
import httpx
from src.core.cache import cache
from src.core.bedrock import bedrock
import uvicorn

app = FastAPI(title="Observability Agent Service")

class ObservabilityAgent:
    def __init__(self):
        self.prometheus_url = "http://prometheus.monitoring.svc.cluster.local:9090"
        self.system_prompt = self._load_prompt()
    
    def _load_prompt(self) -> str:
        prompt_path = Path(__file__).parent / "prompt.md"
        return prompt_path.read_text()
    
    def process(self, query: str) -> str:
        cache_key = f"query:{hash(query)}"
        cached = cache.get(cache_key, namespace="observability")
        if cached:
            return cached
        
        metrics = self._get_metrics()
        anomalies = self._detect_anomalies()
        
        context = f"Metrics:\n{metrics}\n\nAnomalies:\n{anomalies}\n\nUser Query: {query}"
        
        response = bedrock.invoke(
            messages=[{"role": "user", "content": context}],
            system_prompt=self.system_prompt,
            use_cache=True
        )
        
        cache.set(cache_key, response, ttl=60, namespace="observability")
        return response
    
    def _get_metrics(self) -> str:
        cached = cache.get("metrics", namespace="observability")
        if cached:
            return cached
        
        try:
            query = "up"
            response = httpx.get(
                f"{self.prometheus_url}/api/v1/query",
                params={"query": query},
                timeout=5.0
            )
            data = response.json()
            result = f"Active targets: {len(data.get('data', {}).get('result', []))}"
        except Exception as e:
            result = f"Error fetching metrics: {e}"
        
        cache.set("metrics", result, ttl=60, namespace="observability")
        return result
    
    def _detect_anomalies(self) -> str:
        cached = cache.get("anomalies", namespace="observability")
        if cached:
            return cached
        
        # TODO: Implement anomaly detection algorithm
        anomalies = "No anomalies detected"
        cache.set("anomalies", anomalies, ttl=300, namespace="observability")
        return anomalies

agent = ObservabilityAgent()

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
    return {"status": "healthy", "agent": "observability"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8005)
