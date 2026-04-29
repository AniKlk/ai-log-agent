import asyncio
import contextvars
import logging
import time
import uuid

from fastapi import APIRouter, HTTPException, Request

from app.models.request import AnalyzeRequest
from app.models.response import AnalyzeResponse

logger = logging.getLogger(__name__)

request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")

router = APIRouter()


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest, request: Request) -> AnalyzeResponse:
    rid = str(uuid.uuid4())
    request_id_ctx.set(rid)

    logger.info("Request received", extra={"request_id": rid, "query_length": len(req.query)})

    orchestrator = request.app.state.orchestrator
    settings = request.app.state.settings
    start = time.monotonic()

    try:
        history = [msg.model_dump() for msg in req.conversation_history] if req.conversation_history else None
        result = await asyncio.wait_for(
            orchestrator.run(req.query, rid, conversation_history=history),
            timeout=settings.ANALYZE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.error("Agent processing timed out", extra={"request_id": rid})
        raise HTTPException(status_code=504, detail="Agent processing timed out")

    duration_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "Request completed",
        extra={"request_id": rid, "duration_ms": duration_ms},
    )

    return AnalyzeResponse(answer=result, request_id=rid, duration_ms=duration_ms)
