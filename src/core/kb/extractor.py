"""Extract KbDelta drafts from RCA results using Sonnet."""
import json

from src.core.bedrock import bedrock
from src.core.kb.models import KbDelta
from src.core.kb.redactor import redact
from src.core.investigation import RCAResult
from src.core.logger import logger

EXTRACTOR_PROMPT = """You extract structured knowledge items from RCA (Root Cause Analysis) results.

Given an RCA result, identify 1-3 reusable knowledge items (KbDelta) covering: troubleshooting patterns, decisions taken, infrastructure facts.

Output JSON ONLY (no preamble):
{
  "deltas": [
    {
      "action": "create",
      "type": "troubleshooting" | "decision" | "pattern" | "infrastructure",
      "title": "short descriptive title",
      "content": "detailed content with WHEN/THEN structure if troubleshooting",
      "tags": ["tag1", "tag2"],
      "service_name": "service if specific, null if general",
      "confidence": 0.0-1.0,
      "reasoning": "why this is worth saving"
    }
  ]
}

RULES:
- Confidence reflects how reusable/general the item is, not the RCA confidence.
- 'troubleshooting' = symptom->diagnosis->fix. 'decision' = architectural choice. 'pattern' = recurring scenario. 'infrastructure' = factual config.
- Skip noise (single-incident specifics with no reuse value) — return empty deltas."""


async def extract_deltas(rca: RCAResult) -> list[KbDelta]:
    rca_text = redact(json.dumps(rca.to_dict()))
    user_msg = f"RCA result:\n{rca_text}\n\nExtract reusable knowledge items as JSON."
    try:
        response = await bedrock.invoke(
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=EXTRACTOR_PROMPT,
            temperature=0.2,
            role="agent",  # spec 11: extraction uses agent tier (Sonnet)
        )
        start = response.find("{")
        end = response.rfind("}")
        if start < 0:
            return []
        parsed = json.loads(response[start:end + 1])
        deltas = []
        for d in parsed.get("deltas", []) or []:
            try:
                deltas.append(KbDelta(
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
        return deltas
    except Exception as e:
        logger.warning("Extractor failed", extra={"error": str(e)})
        return []
