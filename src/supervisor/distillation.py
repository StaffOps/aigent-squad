"""Distillation pipeline orchestrator. Fire-and-forget after investigation."""
import uuid

from src.core.config import settings
from src.core.investigation import RCAResult
from src.core.kb.models import KbItem, KbProvenance, KbStatus
from src.core.kb.extractor import extract_deltas
from src.core.kb.enricher import enrich_deltas
from src.core.kb.validator import decide_status
from src.core.kb.embedder import embed
from src.core.kb.store import kb_store
from src.core.kb.budget import check_budget, record_cost
from src.core.kb.redactor import redact
from src.core.logger import logger
from src.core.metrics import kb_distillation_cost, kb_items_created
from src.core.model_tier import resolve_model

ESTIMATED_COST_PER_DISTILL = 0.27  # USD (extractor + enricher + embedding)


def _valid_investigation_id(investigation_id: str | None) -> str | None:
    """The id when it is a UUID (kb_provenance.source_investigation_id), else None."""
    if not investigation_id:
        return None
    try:
        return str(uuid.UUID(investigation_id))
    except (ValueError, TypeError, AttributeError):
        logger.warning("Investigation id is not a UUID; KB provenance not written")
        return None


def _role_model(role: str) -> str | None:
    """Model id for an LLM role, or None when it cannot be resolved (best-effort)."""
    try:
        return resolve_model(role)
    except Exception:
        return None


async def distill_rca(rca: RCAResult, investigation_id: str | None = None) -> None:
    """Run distillation pipeline on a finished RCA. Best-effort: never raises.

    ``investigation_id`` (the ``InvestigationState.id``) is stored in the item's
    ``metadata`` and, when it is a UUID, in a ``kb_provenance`` row.
    """
    try:
        if not settings.kb_enabled:
            return
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
        provenance_id = _valid_investigation_id(investigation_id)
        # Same roles extract_deltas / enrich_deltas pass to the LLM.
        extractor_model = _role_model("agent")
        enricher_model = _role_model("synthesis")

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
                provenance = None
                if provenance_id is not None:
                    provenance = KbProvenance(
                        source_investigation_id=provenance_id,
                        confidence=delta.confidence,
                        extractor_model=extractor_model,
                        enricher_model=enricher_model,
                    )
                await kb_store.insert(item, provenance=provenance)
                kb_items_created.add(1, {"type": item.type, "status": item.status})
                logger.info("KB item distilled", extra={
                    "type": item.type, "status": item.status, "title": item.title[:80],
                })
            except Exception as e:
                logger.warning("KB item insert failed", extra={"error": str(e)})

        record_cost(ESTIMATED_COST_PER_DISTILL)
        kb_distillation_cost.add(ESTIMATED_COST_PER_DISTILL)
    except Exception as e:
        logger.warning("Distillation pipeline failed (best-effort)", extra={"error": str(e)})
