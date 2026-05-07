import asyncio
import httpx
from azure.identity.aio import AzureCliCredential, ChainedTokenCredential, DefaultAzureCredential
from openai import AsyncOpenAI
from app.config import Settings

async def main():
    settings = Settings()
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

    client = AsyncOpenAI(
        base_url=base_url,
        api_key="azure-ad",
        http_client=httpx.AsyncClient(auth=AzureTokenAuth()),
    )

    tools = [
        {
            "type": "function",
            "function": {
                "name": "testTool",
                "description": "A test tool",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "string"}
                    },
                    "required": ["x"]
                }
            }
        }
    ]

    try:
        resp = await client.chat.completions.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "ping"},
            ],
            tools=tools,
            temperature=0,
            max_tokens=32,
        )
        print("OK", resp.choices[0].finish_reason)
    except Exception as exc:
        print("ERROR_TYPE:", type(exc).__name__)
        print("ERROR:", str(exc))
        print("STATUS:", getattr(exc, "status_code", None))
        body = getattr(exc, "body", None)
        if body is not None:
            print("BODY:", body)
    finally:
        await client.close()
        await openai_credential.close()

asyncio.run(main())
