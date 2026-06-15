"""Enrich KbDelta drafts using Opus (or fallback to Sonnet)."""
import json
import os

from src.core.bedrock import bedrock
from src.core.kb.models import KbDelta
from src.core.kb.redactor import redact
from src.core.investigation import RCAResult
from src.core.logger import logger

ENRICHER_PROMPT = """You refine knowledge base drafts. You receive Sonnet's drafts plus the original RCA. Your job: generalize, identify cross-incident patterns, improve wording for future reuse.

Return the SAME JSON schema as input (deltas[]) but with:
- Better titles (clear, searchable)
- Generalized content (less incident-specific, more reusable)
- Updated confidence (lower if too specific, higher if confirmed pattern)
- Drop drafts that don't add value (filter, don't keep all)

Return JSON ONLY."""


async def enrich_deltas(drafts: list[KbDelta], rca: RCAResult) -> list[KbDelta]:
    if not drafts:
        return []
    drafts_json = json.dumps([d.__dict__ for d in drafts])
    rca_text = redact(json.dumps(rca.to_dict()))
    user_msg = f"Drafts:\n{drafts_json}\n\nOriginal RCA:\n{rca_text}\n\nReturn refined deltas."
    try:
        response = await bedrock.invoke(
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=ENRICHER_PROMPT,
            temperature=0.3,
        )
        start = response.find("{")
        end = response.rfind("}")
        if start < 0:
            return drafts
        parsed = json.loads(response[start:end + 1])
        refined = []
        for d in parsed.get("deltas", []) or []:
            try:
                refined.append(KbDelta(
                    action=d.get("action", "create"),
                    type=d.get("type", "troubleshooting"),
                    title=d.get("title", ""),
                    content=d.get("content", ""),
                    tags=d.get("tags", []) or [],
                    service_name=d.get("service_name"),
                    confidence=float(d.get("confidence", 0.0)),
                    reasoning=d.get("reasoning", ""),
                ))
            except Exception:
                continue
        return refined or drafts
    except Exception as e:
        logger.warning("Enricher failed (using drafts)", extra={"error": str(e)})
        return drafts
