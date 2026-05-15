"""
setup_qdrant.py — One-time script to create the Qdrant collection
and seed it with sample documents.

Run once before starting the worker:
    python setup_qdrant.py
"""

import asyncio
from qdrant_client import models
from qdrant_client.async_qdrant_client import AsyncQdrantClient
from sentence_transformers import SentenceTransformer
from config import settings

# Sample knowledge base documents
SAMPLE_DOCUMENTS = [
    {"text": "The transformer architecture uses self-attention mechanisms to process sequences in parallel.", "source": "ml_textbook"},
    {"text": "Distributed systems require consensus algorithms like Raft or Paxos for fault tolerance.", "source": "dist_systems"},
    {"text": "Vector databases store high-dimensional embeddings and support approximate nearest neighbor search.", "source": "ml_textbook"},
    {"text": "Load balancers distribute traffic across multiple servers using algorithms like round-robin or least connections.", "source": "dist_systems"},
    {"text": "Semantic caching stores embeddings of previous queries to avoid redundant computation.", "source": "ml_textbook"},
    {"text": "Redis Streams provide a persistent, log-based message queue with consumer group support.", "source": "databases"},
    {"text": "vLLM uses PagedAttention to efficiently manage GPU memory during inference.", "source": "ml_textbook"},
    {"text": "Horizontal scaling adds more machines, vertical scaling adds more resources to existing machines.", "source": "dist_systems"},
    {"text": "The GIL in CPython prevents true thread parallelism for CPU-bound tasks.", "source": "python_docs"},
    {"text": "asyncio is cooperative multitasking: coroutines yield control at await points.", "source": "python_docs"},
]


async def main():
    print(f"Connecting to Qdrant at {settings.QDRANT_HOST}:{settings.QDRANT_PORT}...")
    client = AsyncQdrantClient(host=settings.QDRANT_HOST, port=settings.QDRANT_PORT)

    print(f"Loading model: {settings.EMBEDDING_MODEL}")
    model = SentenceTransformer(settings.EMBEDDING_MODEL)

    # Determine embedding dimension
    sample_emb = model.encode("test")
    dim = len(sample_emb)
    print(f"Embedding dimension: {dim}")

    # Create collection (idempotent)
    existing = [c.name for c in (await client.get_collections()).collections]
    if settings.QDRANT_COLLECTION in existing:
        print(f"Collection '{settings.QDRANT_COLLECTION}' already exists, skipping creation.")
    else:
        await client.create_collection(
            collection_name=settings.QDRANT_COLLECTION,
            vectors_config=models.VectorParams(
                size=dim,
                distance=models.Distance.COSINE,
            ),
        )
        print(f"Created collection '{settings.QDRANT_COLLECTION}'.")

    # Embed and upsert sample documents
    print(f"Embedding and upserting {len(SAMPLE_DOCUMENTS)} documents...")
    texts = [d["text"] for d in SAMPLE_DOCUMENTS]
    embeddings = model.encode(texts, show_progress_bar=True)

    points = [
        models.PointStruct(
            id=i,
            vector=embeddings[i].tolist(),
            payload=SAMPLE_DOCUMENTS[i],
        )
        for i in range(len(SAMPLE_DOCUMENTS))
    ]

    await client.upsert(collection_name=settings.QDRANT_COLLECTION, points=points)
    print(f"Upserted {len(points)} points.")

    await client.close()
    print("Done. Qdrant is ready.")


if __name__ == "__main__":
    asyncio.run(main())
