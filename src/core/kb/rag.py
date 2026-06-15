"""RAG injection: query KB by symptom embedding, format <similar_cases> block."""
from src.core.kb.store import kb_store
from src.core.kb.embedder import embed
from src.core.logger import logger


async def inject_similar_cases(
    symptom: str, service_name: str | None = None,
    top_k: int = 3, threshold: float = 0.75,
) -> str:
    """Returns a <similar_cases> XML block to inject in the synthesizer prompt, or empty string."""
    try:
        emb = await embed(symptom)
        if emb is None:
            return ""
        items = await kb_store.search_similar(emb, top_k=top_k, threshold=threshold, service_name=service_name)
        if not items:
            return ""
        blocks = []
        for item in items:
            blocks.append(
                f'<case type="{item.type}" service="{item.service_name or "general"}">\n'
                f"<title>{item.title}</title>\n"
                f"<content>{item.content}</content>\n</case>"
            )
        return (
            "<similar_cases>\n" + "\n".join(blocks) + "\n</similar_cases>\n\n"
            "NOTE: similar cases are PRIORS; verify against current evidence before concluding."
        )
    except Exception as e:
        logger.warning("RAG injection failed (fail-open)", extra={"error": str(e)})
        return ""
