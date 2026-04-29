import logging
import sys
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

import httpx
from azure.cosmos.aio import CosmosClient
from azure.identity.aio import DefaultAzureCredential
from azure.monitor.query.aio import LogsQueryClient
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI

from app.agent.orchestrator import AgentOrchestrator
from app.api.routes import router
from app.config import Settings
from app.tools.chat_history import GetChatHistoryTool
from app.tools.cosmos_query import QueryCosmosTool
from app.tools.kql import QueryKQLTool
from app.tools.session_log_stats import GetSessionLogStatsTool
from app.tools.registry import ToolRegistry
from app.tools.session_data import GetSessionDataTool
from app.tools.timeline import GetSessionTimelineTool

logger = logging.getLogger(__name__)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
        stream=sys.stdout,
    )
    # Suppress verbose Azure SDK HTTP logging
    logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
    logging.getLogger("azure.cosmos._cosmos_http_logging_policy").setLevel(logging.WARNING)
    logging.getLogger("azure.identity").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    _configure_logging(settings.LOG_LEVEL)

    credential = DefaultAzureCredential()

    # --- Refreshing Azure AD token for OpenAI ---
    # Tokens expire after ~60 min; we cache and re-fetch when within 60 s of expiry.
    _OPENAI_SCOPE = "https://cognitiveservices.azure.com/.default"
    _cached_token: dict = {"token": "", "expires_on": 0.0}

    async def _get_openai_token() -> str:
        if time.monotonic() >= _cached_token["expires_on"] - 60:
            t = await credential.get_token(_OPENAI_SCOPE)
            _cached_token["token"] = t.token
            _cached_token["expires_on"] = float(t.expires_on)
        return _cached_token["token"]

    class _AzureTokenAuth(httpx.Auth):
        """Injects a fresh Azure AD Bearer token into every OpenAI HTTP request."""
        requires_request_body = False

        async def async_auth_flow(self, request: httpx.Request) -> AsyncGenerator[httpx.Request, httpx.Response]:
            request.headers["Authorization"] = f"Bearer {await _get_openai_token()}"
            yield request

        def auth_flow(self, request: httpx.Request):  # type: ignore[override]
            # Sync fallback — not used by AsyncOpenAI but required by httpx.Auth ABC
            raise RuntimeError("Sync auth not supported; use async client only")

    # Seed the cache now so the first request doesn't block under load
    initial_token = await credential.get_token(_OPENAI_SCOPE)
    _cached_token["token"] = initial_token.token
    _cached_token["expires_on"] = float(initial_token.expires_on)

    base_url = settings.AZURE_OPENAI_ENDPOINT.rstrip("/")
    if not base_url.endswith("/openai/v1"):
        base_url += "/openai/v1"

    openai_client = AsyncOpenAI(
        base_url=base_url,
        api_key="azure-ad",  # placeholder — actual auth is handled by _AzureTokenAuth
        http_client=httpx.AsyncClient(auth=_AzureTokenAuth()),
    )

    logs_client = LogsQueryClient(credential)
    cosmos_credential = settings.COSMOS_KEY if settings.COSMOS_KEY else credential
    cosmos_client = CosmosClient(url=settings.COSMOS_ENDPOINT, credential=cosmos_credential)

    registry = ToolRegistry()
    registry.register(
        GetSessionDataTool(
            logs_client,
            settings.PROPROCTOR_WORKSPACE_ID,
            settings.INFRA_WORKSPACE_ID,
            cosmos_client,
        )
    )
    registry.register(
        GetSessionTimelineTool(
            logs_client,
            settings.PROPROCTOR_WORKSPACE_ID,
            settings.INFRA_WORKSPACE_ID,
            cosmos_client,
        )
    )
    registry.register(GetChatHistoryTool(cosmos_client))
    registry.register(
        QueryKQLTool(logs_client, settings.PROPROCTOR_WORKSPACE_ID, settings.INFRA_WORKSPACE_ID)
    )
    registry.register(QueryCosmosTool(cosmos_client))
    registry.register(GetSessionLogStatsTool(cosmos_client))

    orchestrator = AgentOrchestrator(openai_client, registry, settings)

    app.state.orchestrator = orchestrator
    app.state.settings = settings

    logger.info("Application started — all tools registered")

    yield

    await logs_client.close()
    await cosmos_client.close()
    await credential.close()
    await openai_client.close()
    logger.info("Application shutdown — clients closed")


def create_app() -> FastAPI:
    app = FastAPI(title="AI Log Agent", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=Settings().cors_origin_list,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    app.include_router(router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    return app


app = create_app()
