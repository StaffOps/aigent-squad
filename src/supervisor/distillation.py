"""Distillation pipeline orchestrator. Fire-and-forget after investigation."""
from src.core.investigation import RCAResult
from src.core.kb.models import KbItem, KbStatus
from src.core.kb.extractor import extract_deltas
from src.core.kb.enricher import enrich_deltas
from src.core.kb.validator import decide_status
from src.core.kb.embedder import embed
from src.core.kb.store import kb_store
from src.core.kb.budget import check_budget, record_cost
from src.core.kb.redactor import redact
from src.core.logger import logger

ESTIMATED_COST_PER_DISTILL = 0.27  # USD (extractor + enricher + embedding)


async def distill_rca(rca: RCAResult, investigation_id: str | None = None):
    """Run distillation pipeline on a finished RCA. Best-effort: never raises."""
    try:
        if rca.confidence == "baixa":
            logger.info("Skipping distillation (low confidence RCA)")
            return
        if not check_budget(ESTIMATED_COST_PER_DISTILL):
            logger.warning("KB monthly budget exhausted; skipping distillation")
            return

        drafts = await extract_deltas(rca)
        if not drafts:
            return
        refined = await enrich_deltas(drafts, rca)

        for delta in refined:
            try:
                status = decide_status(delta)
                if status == KbStatus.REJECTED.value:
                    continue
                emb = await embed(redact(f"{delta.title}\n{delta.content}"))
                item = KbItem(
                    type=delta.type,
                    title=redact(delta.title),
                    content=redact(delta.content),
                    tags=delta.tags,
                    service_name=delta.service_name,
                    embedding=emb,
                    metadata={"investigation_id": investigation_id, "reasoning": delta.reasoning},
                    confidence_score=delta.confidence,
                    status=status,
                )
                await kb_store.insert(item)
                logger.info("KB item distilled", extra={
                    "type": item.type, "status": item.status, "title": item.title[:80],
                })
            except Exception as e:
                logger.warning("KB item insert failed", extra={"error": str(e)})

        record_cost(ESTIMATED_COST_PER_DISTILL)
    except Exception as e:
        logger.warning("Distillation pipeline failed (best-effort)", extra={"error": str(e)})
