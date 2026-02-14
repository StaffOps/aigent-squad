from fastapi import FastAPI
from pydantic import BaseModel
from pathlib import Path
from kubernetes import client, config
from src.core.cache import cache
from src.core.bedrock import bedrock
import uvicorn

app = FastAPI(title="Kubernetes Agent Service")

class KubernetesAgent:
    def __init__(self):
        try:
            config.load_incluster_config()  # Try in-cluster first (EKS)
        except:
            config.load_kube_config()  # Fallback to local kubeconfig
        self.v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self.system_prompt = self._load_prompt()
    
    def _load_prompt(self) -> str:
        prompt_path = Path(__file__).parent / "prompt.md"
        return prompt_path.read_text()
    
    def process(self, query: str) -> str:
        cache_key = f"query:{hash(query)}"
        cached = cache.get(cache_key, namespace="k8s")
        if cached:
            return cached
        
        cluster_state = self._get_cluster_state()
        context = f"Cluster State:\n{cluster_state}\n\nUser Query: {query}"
        
        response = bedrock.invoke(
            messages=[{"role": "user", "content": context}],
            system_prompt=self.system_prompt,
            use_cache=True
        )
        
        cache.set(cache_key, response, ttl=60, namespace="k8s")
        return response
    
    def _get_cluster_state(self) -> str:
        cached = cache.get("cluster_state", namespace="k8s")
        if cached:
            return cached
        
        pods = self.v1.list_pod_for_all_namespaces()
        nodes = self.v1.list_node()
        
        state = {
            "nodes": len(nodes.items),
            "pods": len(pods.items),
            "namespaces": len(set(p.metadata.namespace for p in pods.items))
        }
        
        result = str(state)
        cache.set("cluster_state", result, ttl=60, namespace="k8s")
        return result

agent = KubernetesAgent()

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
    return {"status": "healthy", "agent": "kubernetes"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8002)
