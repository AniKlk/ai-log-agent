import asyncio
import httpx
from azure.identity.aio import DefaultAzureCredential
from openai import AsyncOpenAI
from app.config import Settings

async def main():
    settings = Settings()
    credential = DefaultAzureCredential()

    cached = {"token": "", "expires_on": 0.0}
    scope = "https://cognitiveservices.azure.com/.default"

    async def get_token():
        token = await credential.get_token(scope)
        cached["token"] = token.token
        cached["expires_on"] = float(token.expires_on)
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

    print("base_url:", base_url)
    print("deployment:", settings.AZURE_OPENAI_DEPLOYMENT)

    client = AsyncOpenAI(
        base_url=base_url,
        api_key="azure-ad",
        http_client=httpx.AsyncClient(auth=AzureTokenAuth()),
    )

    try:
        resp = await client.chat.completions.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            messages=[{"role": "user", "content": "ping"}],
            temperature=0,
            max_tokens=16,
        )
        print("OK", resp.choices[0].message.content)
    except Exception as exc:
        print("ERROR_TYPE:", type(exc).__name__)
        print("ERROR:", str(exc))
        print("STATUS:", getattr(exc, "status_code", None))
        body = getattr(exc, "body", None)
        if body is not None:
            print("BODY:", body)
    finally:
        await client.close()
        await credential.close()

asyncio.run(main())
