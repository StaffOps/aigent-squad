from fastapi import FastAPI
from pydantic import BaseModel
from pathlib import Path
from src.core.cache import cache
from src.core.bedrock import bedrock
from src.core.docs_portal import DocsPortalClient
import uvicorn

app = FastAPI(title="DevOps Agent Service")

class DevOpsAgent:
    def __init__(self):
        self.system_prompt = self._load_prompt()
        self.docs_client = DocsPortalClient()
    
    def _load_prompt(self) -> str:
        prompt_path = Path(__file__).parent / "prompt.md"
        return prompt_path.read_text()
    
    def process(self, query: str) -> str:
        cache_key = f"query:{hash(query)}"
        cached = cache.get(cache_key, namespace="devops")
        if cached:
            return cached
        
        # Search documentation portal
        docs_results = self._search_docs(query)
        
        # Get pipeline status
        pipeline_status = self._get_pipeline_status()
        
        context = f"""Documentation Search Results:\n{docs_results}\n\nPipeline Status:\n{pipeline_status}\n\nUser Query: {query}"""
        
        response = bedrock.invoke(
            messages=[{"role": "user", "content": context}],
            system_prompt=self.system_prompt,
            use_cache=True
        )
        
        cache.set(cache_key, response, ttl=300, namespace="devops")
        return response
    
    def _search_docs(self, query: str) -> str:
        """Search documentation portal for relevant docs"""
        try:
            results = self.docs_client.search(query, limit=5)
            return self.docs_client.format_search_results(results)
        except Exception as e:
            return f"Documentation search unavailable: {e}"
    
    def _get_pipeline_status(self) -> str:
        cached = cache.get("pipeline_status", namespace="devops")
        if cached:
            return cached
        
        # TODO: Integrate with GitHub Actions/GitLab CI API
        status = "All pipelines healthy"
        cache.set("pipeline_status", status, ttl=60, namespace="devops")
        return status

agent = DevOpsAgent()

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
    return {"status": "healthy", "agent": "devops"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8004)
