import asyncio
import httpx
from azure.cosmos.aio import CosmosClient
from azure.identity.aio import AzureCliCredential, ChainedTokenCredential, DefaultAzureCredential
from azure.monitor.query.aio import LogsQueryClient
from openai import AsyncOpenAI

from app.agent.orchestrator import AgentOrchestrator
from app.config import Settings
from app.tools.chat_history import GetChatHistoryTool
from app.tools.cosmos_query import QueryCosmosTool
from app.tools.exam_status_counts import GetExamStatusCountsTool
from app.tools.kql import QueryKQLTool
from app.tools.registry import ToolRegistry
from app.tools.session_data import GetSessionDataTool
from app.tools.session_log_stats import GetSessionLogStatsTool
from app.tools.timeline import GetSessionTimelineTool


async def main():
    settings = Settings()
    credential = DefaultAzureCredential()
    openai_credential = ChainedTokenCredential(
        AzureCliCredential(),
        DefaultAzureCredential(exclude_azure_cli_credential=True),
    )

    scope = "https://cognitiveservices.azure.com/.default"

    async def get_token():
        token = await openai_credential.get_token(scope)
        return token.token

    class AzureTokenAuth(httpx.Auth):
        requires_request_body = False
        async def async_auth_flow(self, request: httpx.Request):
            request.headers["Authorization"] = f"Bearer {await get_token()}"
            yield request
        def auth_flow(self, request):
            raise RuntimeError("sync not supported")

    base_url = settings.AZURE_OPENAI_ENDPOINT.rstrip("/")
    if not base_url.endswith("/openai/v1"):
        base_url += "/openai/v1"

    openai_client = AsyncOpenAI(
        base_url=base_url,
        api_key="azure-ad",
        http_client=httpx.AsyncClient(auth=AzureTokenAuth()),
    )

    logs_client = LogsQueryClient(credential)
    cosmos_credential = settings.COSMOS_KEY if settings.COSMOS_KEY else credential
    cosmos_client = CosmosClient(url=settings.COSMOS_ENDPOINT, credential=cosmos_credential)

    registry = ToolRegistry()
    registry.register(GetSessionDataTool(logs_client, settings.PROPROCTOR_WORKSPACE_ID, settings.INFRA_WORKSPACE_ID, cosmos_client))
    registry.register(GetSessionTimelineTool(logs_client, settings.PROPROCTOR_WORKSPACE_ID, settings.INFRA_WORKSPACE_ID, cosmos_client))
    registry.register(GetChatHistoryTool(cosmos_client))
    registry.register(QueryKQLTool(logs_client, settings.PROPROCTOR_WORKSPACE_ID, settings.INFRA_WORKSPACE_ID))
    registry.register(QueryCosmosTool(cosmos_client))
    registry.register(GetExamStatusCountsTool(cosmos_client))
    registry.register(GetSessionLogStatsTool(cosmos_client))

    orchestrator = AgentOrchestrator(openai_client, registry, settings)

    try:
        result = await orchestrator.run('ping', 'probe-request')
        print('OK_SUMMARY:', result.summary[:200])
        print('TOOLS:', result.tools_invoked)
    except Exception as exc:
        print('ERROR_TYPE:', type(exc).__name__)
        print('ERROR:', str(exc))
        print('STATUS:', getattr(exc, 'status_code', None))
        body = getattr(exc, 'body', None)
        if body is not None:
            print('BODY:', body)
    finally:
        await logs_client.close()
        await cosmos_client.close()
        await openai_client.close()
        await openai_credential.close()
        await credential.close()


asyncio.run(main())
