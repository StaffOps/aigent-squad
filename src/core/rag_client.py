"""
RAG Client for Bedrock Knowledge Base
Retrieves relevant documents before LLM invocation
"""
import boto3
from typing import List, Dict
from src.core.config import settings

class RAGClient:
    def __init__(self, knowledge_base_id: str):
        self.client = boto3.client('bedrock-agent-runtime', region_name=settings.aws_region)
        self.knowledge_base_id = knowledge_base_id
    
    def retrieve(self, query: str, max_results: int = 5) -> List[Dict]:
        """Retrieve relevant documents from Knowledge Base"""
        try:
            response = self.client.retrieve(
                knowledgeBaseId=self.knowledge_base_id,
                retrievalQuery={'text': query},
                retrievalConfiguration={
                    'vectorSearchConfiguration': {
                        'numberOfResults': max_results
                    }
                }
            )
            
            return response.get('retrievalResults', [])
        except Exception as e:
            print(f"RAG retrieval error: {e}")
            return []
    
    def format_context(self, results: List[Dict]) -> str:
        """Format retrieved documents as context"""
        if not results:
            return "No relevant documents found."
        
        context_parts = []
        for i, result in enumerate(results, 1):
            content = result.get('content', {}).get('text', '')
            score = result.get('score', 0)
            metadata = result.get('metadata', {})
            
            context_parts.append(
                f"[Document {i}] (Relevance: {score:.2f})\n"
                f"Source: {metadata.get('source', 'Unknown')}\n"
                f"{content}\n"
            )
        
        return "\n---\n".join(context_parts)
    
    def retrieve_and_format(self, query: str, max_results: int = 5) -> str:
        """Retrieve and format in one call"""
        results = self.retrieve(query, max_results)
        return self.format_context(results)
