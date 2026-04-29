import asyncio
from azure.cosmos.aio import CosmosClient
from app.config import Settings

async def main():
    settings = Settings()
    cosmos_client = CosmosClient(url=settings.COSMOS_ENDPOINT, credential=settings.COSMOS_KEY)
    db = cosmos_client.get_database_client("ExamSession")
    container = db.get_container_client("session-log")
    query = "SELECT TOP 5 * FROM c WHERE ARRAY_LENGTH(c.Entries) > 0"
    items = container.query_items(query=query, partition_key=None)
    async for item in items:
        print("---\n", item)

if __name__ == "__main__":
    asyncio.run(main())
