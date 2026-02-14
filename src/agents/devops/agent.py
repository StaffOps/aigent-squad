from datetime import datetime
from typing import List, Optional
import time
from opentelemetry import trace
from src.core.agent_base import Agent
from src.core.state_store import ConversationMessage
from src.core.cache import cache
from src.core.bedrock import bedrock
from src.core.gitlab_client import gitlab_client
from src.core.logger import logger, log_request, log_response, log_error

tracer = trace.get_tracer(__name__)

class DevOpsAgent(Agent):
    """DevOps Senior Specialist agent with GitLab integration"""
    
    def __init__(self):
        super().__init__(
            agent_id="devops",
            name="DevOps Agent",
            description="Specializes in CI/CD, pipelines, automation, and internal documentation"
        )
        self.system_prompt = self._load_prompt()
        self.gitlab = gitlab_client
    
    async def process_request(
        self,
        input_text: str,
        user_id: str,
        session_id: str,
        chat_history: List[ConversationMessage],
        additional_params: Optional[dict] = None
    ) -> ConversationMessage:
        """Process DevOps-related query with conversation history and GitLab context"""
        
        start_time = time.time()
        
        with tracer.start_as_current_span("devops_agent.process_request") as span:
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
                cached = cache.get(cache_key, namespace="devops")
                if cached:
                    logger.info("Cache hit", extra={"agent_id": self.id, "cache_key": cache_key})
                    return ConversationMessage(
                        role="assistant",
                        content=cached,
                        timestamp=datetime.utcnow().isoformat(),
                        agent_id=self.id
                    )
                
                # Search GitLab documentation
                with tracer.start_as_current_span("devops_agent.search_gitlab"):
                    gitlab_context = self._search_gitlab_context(input_text)
                
                # Get pipeline status
                with tracer.start_as_current_span("devops_agent.get_pipeline_status"):
                    pipeline_status = self._get_pipeline_status()
                
                history_context = self._format_history(chat_history)
                
                # Build enriched context
                context = f"""Company DevOps Context:

**GitLab Documentation & Code:**
{gitlab_context}

**Pipeline Status:**
{pipeline_status}

**Conversation History:**
{history_context}

**Current Query:** {input_text}

**Important:**
- Documentation URL: {self.gitlab.get_docs_url()}
- Always reference Company's internal practices
- Cite specific documentation when available
- Suggest improvements aligned with our culture"""
                
                with tracer.start_as_current_span("devops_agent.bedrock_invoke"):
                    response = bedrock.invoke(
                        messages=[{"role": "user", "content": context}],
                        system_prompt=self.system_prompt,
                        use_cache=True
                    )
                
                cache.set(cache_key, response, ttl=300, namespace="devops")
                
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
    
    def _search_gitlab_context(self, query: str) -> str:
        """Search GitLab for relevant documentation and code across ENTIRE Company"""
        try:
            # Priority 1: Search in documentation (most important)
            docs_results = self.gitlab.search_documentation(query, limit=5)
            
            # Priority 2: Search in devops group
            devops_results = self.gitlab.search_code(query, group=self.gitlab.devops_group)[:3]
            
            # Priority 3: Search in infrastructure group
            infra_results = self.gitlab.search_code(query, group=self.gitlab.infra_group)[:3]
            
            # Priority 4: Search entire Company (if needed)
            company_results = self.gitlab.search_in_company(query, limit=5)
            
            context_parts = []
            
            if docs_results:
                context_parts.append("**📚 Documentation Found (devops-docs):**")
                for result in docs_results[:3]:
                    context_parts.append(f"- {result.get('filename', 'N/A')}: {result.get('data', '')[:200]}...")
            
            if devops_results:
                context_parts.append("\n**🔧 DevOps Projects:**")
                for result in devops_results:
                    context_parts.append(f"- {result.get('filename', 'N/A')} in {result.get('project_id', 'N/A')}")
            
            if infra_results:
                context_parts.append("\n**🏗️ Infrastructure Code:**")
                for result in infra_results:
                    context_parts.append(f"- {result.get('filename', 'N/A')} in {result.get('project_id', 'N/A')}")
            
            if company_results and not (docs_results or devops_results or infra_results):
                context_parts.append("\n**🔍 Other Company Projects:**")
                for result in company_results[:3]:
                    context_parts.append(f"- {result.get('filename', 'N/A')} in {result.get('project_id', 'N/A')}")
            
            if not context_parts:
                return "No specific documentation found in Company GitLab. Using general DevOps knowledge."
            
            context_parts.append("\n**Note**: Full access to entire Company organization available if more context needed.")
            
            return "\n".join(context_parts)
            
        except Exception as e:
            logger.warning("Error searching GitLab", extra={"error": str(e)})
            return "GitLab search unavailable. Using general knowledge."
    
    def _get_pipeline_status(self) -> str:
        cached = cache.get("pipeline_status", namespace="devops")
        if cached:
            return cached
        
        # TODO: Integrate with GitLab CI API to get real pipeline status
        status = "All pipelines healthy (GitLab integration pending)"
        cache.set("pipeline_status", status, ttl=60, namespace="devops")
        return status
    
    def _format_history(self, messages: List[ConversationMessage]) -> str:
        if not messages:
            return "No previous conversation"
        return "\n".join([f"{msg.role}: {msg.content}" for msg in messages[-5:]])
