"""
worker.py — Regional Worker (Orchestrator) Node
Pipeline: Embed → RAG (Qdrant) → Dispatch (Redis Queue)
"""

import asyncio
import json
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from qdrant_client import models
from qdrant_client.async_qdrant_client import AsyncQdrantClient
from sentence_transformers import SentenceTransformer

from config import settings
from models import InferRequest, InferResponse, HealthResponse
# from metrics import MetricsCollector

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(process)d] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("worker")

# ── Process-level singletons ─────────────────────────────────────────────────
# These are initialized ONCE per process in lifespan, never per-request.

_model: SentenceTransformer = None
_executor: ThreadPoolExecutor = None
_qdrant: AsyncQdrantClient = None
_redis: aioredis.Redis = None
_semaphore: asyncio.Semaphore = None
# _metrics: MetricsCollector = None


# ── Lifespan: startup & shutdown ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model, _executor, _semaphore, _redis, _qdrant  

    logger.info(f"[PID {os.getpid()}] Starting worker node...")

    # 1. Load embedding model — expensive, done once per process
    logger.info(f"Loading embedding model: {settings.EMBEDDING_MODEL}")
    _model = SentenceTransformer(settings.EMBEDDING_MODEL)
    logger.info("Embedding model loaded.")

    # 2. Thread pool for CPU-bound embedding
    #    max_workers = CPU cores — more threads just means more GIL contention
    _executor = ThreadPoolExecutor(
        max_workers=settings.THREAD_POOL_SIZE,
        thread_name_prefix="embed-worker",
    )

    # 3. Async Qdrant client with connection pool
    if settings.QDRANT_URL:
        logger.info(f"Connecting to remote Qdrant at {settings.QDRANT_URL}")
        _qdrant = AsyncQdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY,
            timeout=settings.QDRANT_TIMEOUT_SEC,
            limits=httpx.Limits(
                max_connections=settings.QDRANT_MAX_CONNECTIONS,
                max_keepalive_connections=settings.QDRANT_KEEPALIVE_CONNECTIONS,
            ),
        )
    else:
        logger.info(f"Connecting to local Qdrant at {settings.QDRANT_HOST}:{settings.QDRANT_PORT}")
        _qdrant = AsyncQdrantClient(
            host=settings.QDRANT_HOST,
            port=settings.QDRANT_PORT,
            timeout=settings.QDRANT_TIMEOUT_SEC,
            limits=httpx.Limits(
                max_connections=settings.QDRANT_MAX_CONNECTIONS,
                max_keepalive_connections=settings.QDRANT_KEEPALIVE_CONNECTIONS,
            ),
        )

    # 4. Async Redis client with connection pool
    _redis = await aioredis.from_url(
        settings.REDIS_URL,
        max_connections=settings.REDIS_MAX_CONNECTIONS,
        decode_responses=False,  # we send raw bytes
    )

    # 5. Semaphore — backpressure: caps in-flight requests per process
    #    Without this, a slow Qdrant will cause unbounded memory growth

    _semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_PER_PROCESS)


    logger.info(
        f"[PID {os.getpid()}] Worker ready. "
        f"model={settings.EMBEDDING_MODEL}, "
        f"threads={settings.THREAD_POOL_SIZE}, "
        f"max_concurrent={settings.MAX_CONCURRENT_PER_PROCESS}"
    )

    yield  # ← app runs here

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info(f"[PID {os.getpid()}] Shutting down...")
    _executor.shutdown(wait=True)
    # await _qdrant.close()
    # await _redis.aclose()
    logger.info(f"[PID {os.getpid()}] Shutdown complete.")


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Distributed Embedding Worker",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Middleware: request timing ────────────────────────────────────────────────

@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.2f}"
    response.headers["X-Worker-PID"] = str(os.getpid())
    return response


# ── Pipeline steps ────────────────────────────────────────────────────────────

async def step_embed(text: str) -> list[float]:
    """
    CPU-BOUND — runs in ThreadPoolExecutor.

    We use run_in_executor so the event loop is NOT blocked during the
    ~20ms of matrix multiplication. Other requests' Qdrant/Redis awaits
    continue running freely on the main thread while this crunches in a
    background thread.

    sentence-transformers releases the GIL during numpy ops, so multiple
    threads DO run embedding truly in parallel within this process.
    """
    loop = asyncio.get_running_loop()
    t0 = time.perf_counter()

    embedding = await loop.run_in_executor(
        _executor,
        _model.encode,  # callable
        text,           # positional arg
    )

    elapsed_ms = (time.perf_counter() - t0) * 1000
    # _metrics.record_embed_latency(elapsed_ms)
    logger.debug(f"Embed done in {elapsed_ms:.1f}ms")
    logger.debug(f"Embedding vectorrr (first 5 dims): {embedding[:5]}")
    return embedding.tolist()


async def step_rag(embedding: list[float], request_id: str) -> list[dict]:
    """
    I/O-BOUND — pure async, no thread needed.

    The event loop fires the Qdrant TCP request and immediately yields.
    While this request waits for the network response (~10-30ms),
    the event loop runs other coroutines — other embeds completing,
    other Qdrant responses arriving, other dispatches completing.

    This is the power of async: zero threads, zero blocking,
    hundreds of Qdrant queries in flight simultaneously.
    """
    t0 = time.perf_counter()

    try:
        results = await _qdrant.search(
            collection_name=settings.QDRANT_COLLECTION,
            query_vector=embedding,
            limit=settings.RAG_TOP_K,
            score_threshold=settings.RAG_SCORE_THRESHOLD,
            with_payload=True,
        )
    except Exception as e:
        logger.error(f"[{request_id}] Qdrant search failed: {e}")
        raise HTTPException(status_code=503, detail="Vector store unavailable")

    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.debug(f"[{request_id}] RAG returned {len(results)} chunks in {elapsed_ms:.1f}ms")
    return [
        {
            "id": str(r.id),
            "text": r.payload.get("text", ""),
            "score": round(r.score, 4),
            "source": r.payload.get("source", "unknown"),
        }
        for r in results
    ]




async def step_dispatch(
    request_id: str,
    original_text: str,
    embedding: list[float],
    context_chunks: list[dict],
) -> None:
    """
    I/O-BOUND — async Redis Streams publish.

    Redis Streams (XADD) give us:
    - Persistence: messages survive a worker crash
    - Consumer groups: vLLM workers can ack messages
    - Backpressure: MAXLEN caps the stream size

    Like Qdrant, this is a pure network operation.
    The event loop handles hundreds of these concurrently
    with a single thread and zero overhead.
    """
    t0 = time.perf_counter()

    payload = {
        "request_id": request_id,
        "prompt": original_text,
        "context": context_chunks,
        "embedding": embedding,
        "worker_pid": os.getpid(),
        "timestamp": time.time(),
    }

    try:
        await _redis.xadd(
            settings.REDIS_STREAM_KEY,
            {"data": json.dumps(payload)},
            maxlen=settings.REDIS_STREAM_MAXLEN,  # backpressure: drop oldest if full
            approximate=True,
        )
    except Exception as e:
        logger.error(f"[{request_id}] Redis dispatch failed: {e}")
        raise HTTPException(status_code=503, detail="Queue unavailable")

    elapsed_ms = (time.perf_counter() - t0) * 1000
    # _metrics.record_dispatch_latency(elapsed_ms)
    logger.debug(f"[{request_id}] Dispatched in {elapsed_ms:.1f}ms")


# ── Route handlers ────────────────────────────────────────────────────────────

@app.post("/build-context", response_model=InferResponse)
async def handle_infer(body: InferRequest):
    """
    Main Context-Builder endpoint.

    The semaphore provides backpressure: if MAX_CONCURRENT_PER_PROCESS
    requests are already in-flight, new requests wait here instead of
    piling up inside Qdrant/Redis and blowing up memory.
    """
    request_id = str(uuid.uuid4())
    total_start = time.perf_counter()

    # Backpressure: block here if at capacity, don't reject yet
    async with _semaphore:
        # _metrics.increment_active()
        try:
            logger.info(f"[{request_id}] Received: '{body.text[:60]}...'")

            # ── Step 1: Embed (CPU → thread pool) ─────────────────────────
            embedding = await step_embed(body.text)

            # ── Step 2: RAG lookup (I/O → event loop) ─────────────────────
            context_chunks = await step_rag(embedding, request_id)

            # ── Step 3: Dispatch to queue (I/O → event loop) ──────────────
            await step_dispatch(request_id, body.text, embedding, context_chunks)

            total_ms = (time.perf_counter() - total_start) * 1000
            # _metrics.record_total_latency(total_ms)

            logger.info(
                f"[{request_id}] Done in {total_ms:.1f}ms | "
                f"chunks={len(context_chunks)}"
            )

            return InferResponse(
                request_id=request_id,
                status="queued",
                context_chunks=len(context_chunks),
                latency_ms=round(total_ms, 2),
                worker_pid=os.getpid(),
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"[{request_id}] Unexpected error: {e}")
            raise HTTPException(status_code=500, detail="Internal worker error")
        finally:
            # _metrics.decrement_active()
            pass


# @app.get("/health", response_model=HealthResponse)
# async def health_check():
#     """
#     Health check for the load balancer to probe.
#     Checks both Qdrant and Redis connectivity.
#     """
#     qdrant_ok = False
#     redis_ok = False

#     try:
#         await _qdrant.get_collection(settings.QDRANT_COLLECTION)
#         qdrant_ok = True
#     except Exception:
#         pass

#     try:
#         await _redis.ping()
#         redis_ok = True
#     except Exception:
#         pass

#     healthy = qdrant_ok and redis_ok
#     status_code = 200 if healthy else 503

#     return JSONResponse(
#         status_code=status_code,
#         content=HealthResponse(
#             status="healthy" if healthy else "degraded",
#             pid=os.getpid(),
#             qdrant_ok=qdrant_ok,
#             redis_ok=redis_ok,
#             active_requests=_metrics.active_requests,
#             metrics=_metrics.snapshot(),
#         ).model_dump(),
#     )

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


# @app.get("/metrics")
# async def get_metrics():
#     """Exposes latency percentiles and throughput stats."""
#     return _metrics.snapshot()
