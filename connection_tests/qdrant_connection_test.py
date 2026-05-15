import asyncio
from qdrant_client.async_qdrant_client import AsyncQdrantClient


QDRANT_URL="https://ec4e3824-8050-4914-93a3-fa9634463833.us-west-1-0.aws.cloud.qdrant.io:6333"
QDRANT_API_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3MiOiJtIiwic3ViamVjdCI6ImFwaS1rZXk6ZjhhYzc1NGQtNTE1YS00MjdkLWFlNGYtYzEwMzNmMTIzNmNhIn0.iPPlUGikBERA_NmDOF3G-RAKfXebch6WDcESU_rjuSA"

# Optional if using local/self-hosted without URL:
# QDRANT_HOST = "localhost"
# QDRANT_PORT = 6333


async def test_qdrant():
    try:
        client = AsyncQdrantClient(
            url=QDRANT_URL,
            api_key=QDRANT_API_KEY,
            timeout=10,
        )

        # Simple health/info request
        collections = await client.get_collections()

        print("✅ Connected successfully to Qdrant")
        print("Collections:")
        for c in collections.collections:
            print("-", c.name)

        await client.close()

    except Exception as e:
        print("❌ Qdrant connection failed")
        print(type(e).__name__)
        print(e)


if __name__ == "__main__":
    asyncio.run(test_qdrant())