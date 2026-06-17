"""Embedding generation via Bedrock Titan Embed v2."""
import json
import os
import asyncio

import boto3

from src.core.logger import logger

MODEL_ID = os.getenv("BEDROCK_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))
    return _client


async def embed(text: str) -> list[float] | None:
    """Returns 1024-dim embedding or None on error."""
    if not text or not text.strip():
        return None
    try:
        body = json.dumps({"inputText": text[:8000], "dimensions": 1024, "normalize": True})

        def _invoke():
            resp = _get_client().invoke_model(modelId=MODEL_ID, body=body)
            data = json.loads(resp["body"].read())
            return data.get("embedding", [])

        return await asyncio.to_thread(_invoke)
    except Exception as e:
        logger.warning("Embedding failed", extra={"error": str(e), "model": MODEL_ID})
        return None
