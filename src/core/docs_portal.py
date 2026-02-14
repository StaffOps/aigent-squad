import httpx
from typing import Optional, List, Dict
from src.core.config import settings
from src.core.cache import cache

class DocsPortalClient:
    """Client for querying internal documentation portal"""
    
    def __init__(self):
        self.base_url = settings.docs_portal_url
        self.token = settings.docs_portal_token
        self.client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=10.0
        )
    
    def search(self, query: str, limit: int = 5) -> List[Dict]:
        """Search documentation portal"""
        cache_key = f"search:{hash(query)}"
        cached = cache.get(cache_key, namespace="docs")
        if cached:
            return cached
        
        try:
            response = self.client.get("/api/search", params={"q": query, "limit": limit})
            response.raise_for_status()
            results = response.json().get("results", [])
            
            # Cache for 1 hour
            cache.set(cache_key, results, ttl=3600, namespace="docs")
            return results
        except Exception as e:
            return [{"error": f"Failed to search docs: {e}"}]
    
    def get_page(self, page_id: str) -> Optional[Dict]:
        """Get specific documentation page"""
        cache_key = f"page:{page_id}"
        cached = cache.get(cache_key, namespace="docs")
        if cached:
            return cached
        
        try:
            response = self.client.get(f"/api/page/{page_id}")
            response.raise_for_status()
            page = response.json()
            
            # Cache for 1 hour
            cache.set(cache_key, page, ttl=3600, namespace="docs")
            return page
        except Exception as e:
            return {"error": f"Failed to get page: {e}"}
    
    def format_search_results(self, results: List[Dict]) -> str:
        """Format search results for LLM context"""
        if not results:
            return "No documentation found."
        
        formatted = []
        for i, result in enumerate(results, 1):
            if "error" in result:
                return result["error"]
            
            title = result.get("title", "Untitled")
            url = result.get("url", "")
            excerpt = result.get("excerpt", "")
            
            formatted.append(f"{i}. **{title}**\n   URL: {url}\n   {excerpt}\n")
        
        return "\n".join(formatted)
