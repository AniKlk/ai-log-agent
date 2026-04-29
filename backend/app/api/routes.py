import asyncio
import contextvars
import logging
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from openai import AuthenticationError

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
    except AuthenticationError:
        logger.error("Azure OpenAI authentication failed", extra={"request_id": rid})
        raise HTTPException(
            status_code=401,
            detail=(
                "Azure OpenAI authentication failed. Refresh Azure login and ensure access "
                "to the configured Azure OpenAI resource."
            ),
        )
    except asyncio.TimeoutError:
        logger.error("Agent processing timed out", extra={"request_id": rid})
        raise HTTPException(status_code=504, detail="Agent processing timed out")
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        if status_code in {401, 403}:
            logger.error("Upstream authorization failure", extra={"request_id": rid})
            raise HTTPException(
                status_code=401,
                detail="Upstream authorization failed. Please re-authenticate Azure credentials.",
            )
        logger.exception("Unhandled error during analysis", extra={"request_id": rid})
        raise HTTPException(status_code=502, detail="Analysis failed due to upstream dependency error")

    duration_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "Request completed",
        extra={"request_id": rid, "duration_ms": duration_ms},
    )

    return AnalyzeResponse(answer=result, request_id=rid, duration_ms=duration_ms)
