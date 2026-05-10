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
from fastapi.responses import StreamingResponse
from qdrant_client import models
from qdrant_client.async_qdrant_client import AsyncQdrantClient
from sentence_transformers import SentenceTransformer
from typing import Optional

from config import settings
from models import InferRequest
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
_redis_reader: aioredis.Redis = None        # client for the shared Pub/Sub connection
_pubsub: aioredis.client.PubSub = None      # one connection, all result channels multiplexed
_pending: dict[str, asyncio.Queue] = {}     # request_id → Queue fed by the listener
_semaphore: asyncio.Semaphore = None
# _metrics: MetricsCollector = None


# ── Pub/Sub listener ─────────────────────────────────────────────────────────

async def pubsub_listener():
    """
    Single background task that reads every message arriving on any subscribed
    result channel and routes it to the correct waiting SSE coroutine via
    its per-request asyncio.Queue.

    One Redis connection serves all concurrent requests — the Pub/Sub protocol
    multiplexes all channel subscriptions over it, so connection count stays
    constant regardless of how many requests are in-flight.

    Message format published by GPU workers:
        PUBLISH result:{request_id} '{"chunk": "...", "is_final": false}'
    """
    logger.info("Pub/Sub listener started.")
    try:
        async for message in _pubsub.listen():
            if message["type"] != "message":
                continue
            # channel is "result:{request_id}"
            request_id = message["channel"].split(":", 1)[1]
            queue = _pending.get(request_id)
            if queue is None:
                logger.warning(f"[{request_id}] Received pub/sub message but no waiting handler.")
                continue
            await queue.put(message["data"])
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.exception(f"Pub/Sub listener crashed: {e}")


# ── Lifespan: startup & shutdown ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model, _executor, _semaphore, _redis, _redis_reader, _pubsub, _qdrant

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

    # 4. Async Redis client — short-lived ops (XADD to input queue, cache upsert)
    _redis = await aioredis.from_url(
        settings.REDIS_URL,
        max_connections=settings.REDIS_MAX_CONNECTIONS,
        decode_responses=False,
    )

    # 5. Dedicated Redis client for Pub/Sub.
    #    All concurrent result subscriptions are multiplexed over one connection
    #    by the Pub/Sub protocol — no per-request connection needed.
    _redis_reader = await aioredis.from_url(
        settings.REDIS_URL,
        max_connections=settings.REDIS_READER_MAX_CONNECTIONS,
        decode_responses=True,  # channel names and message data are text
    )
    _pubsub = _redis_reader.pubsub()

    # 6. Background listener — reads all incoming Pub/Sub messages and routes
    #    each token to the asyncio.Queue of the waiting SSE coroutine.
    asyncio.create_task(pubsub_listener(), name="pubsub-listener")

    # 8. Semaphore — backpressure: caps in-flight requests per process
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
    # Cancel the background listener task
    for task in asyncio.all_tasks():
        if task.get_name() == "pubsub-listener":
            task.cancel()
    await _pubsub.aclose()
    await _redis.aclose()
    await _redis_reader.aclose()
    # await _qdrant.close()
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


# ── Semantic cache steps ──────────────────────────────────────────────────────

async def step_cache_lookup(embedding: list[float], request_id: str) -> Optional[str]:
    """
    I/O-BOUND — searches the semantic_cache Qdrant collection for a
    previously stored answer whose embedding is close enough to the
    current query.  High threshold (CACHE_SIMILARITY_THRESHOLD > RAG
    threshold) guards against false positives.

    Returns the cached answer string on hit, None on miss.
    """
    t0 = time.perf_counter()
    try:
        results = await _qdrant.search(
            collection_name=settings.CACHE_COLLECTION,
            query_vector=embedding,
            limit=1,
            score_threshold=settings.CACHE_SIMILARITY_THRESHOLD,
            with_payload=True,
        )
    except Exception as e:
        # Cache is non-critical — log and fall through to full pipeline
        logger.warning(f"[{request_id}] Cache lookup failed (non-fatal): {e}")
        return None

    elapsed_ms = (time.perf_counter() - t0) * 1000

    if results:
        answer = results[0].payload.get("answer", "")
        score = round(results[0].score, 4)
        logger.info(f"[{request_id}] Cache HIT (score={score}) in {elapsed_ms:.1f}ms")
        return answer

    logger.debug(f"[{request_id}] Cache miss in {elapsed_ms:.1f}ms")
    return None


async def step_cache_write(
    embedding: list[float],
    prompt: str,
    answer: str,
    request_id: str,
) -> None:
    """
    I/O-BOUND — upserts the completed answer into the semantic_cache
    collection so future similar queries can be served without RAG or GPU.

    Payload stores created_at for future TTL enforcement.
    Errors are non-fatal: a failed write means the next similar query
    simply misses the cache and re-runs the full pipeline.
    """
    point_id = str(uuid.uuid4())
    try:
        await _qdrant.upsert(
            collection_name=settings.CACHE_COLLECTION,
            points=[
                models.PointStruct(
                    id=point_id,
                    vector=embedding,
                    payload={
                        "answer": answer,
                        "prompt": prompt,
                        "model_version": settings.EMBEDDING_MODEL,
                        "created_at": time.time(),
                    },
                )
            ],
        )
        logger.info(f"[{request_id}] Cache write OK (id={point_id})")
    except Exception as e:
        logger.warning(f"[{request_id}] Cache write failed (non-fatal): {e}")


# ── SSE generators ────────────────────────────────────────────────────────────

async def sse_cache_hit(request_id: str, answer: str):
    """
    Yields a single SSE event carrying the cached answer, then signals done.
    Used when step_cache_lookup returns a hit so the client gets the same
    streaming interface regardless of whether the GPU was involved.
    """
    logger.info(f"[{request_id}] Serving cache hit via SSE.")
    yield f"data: {json.dumps({'chunk': answer, 'is_final': True, 'cache_hit': True})}\n\n"
    yield "data: [DONE]\n\n"


async def sse_stream(
    request_id: str,
    embedding: list[float],
    prompt: str,
    queue: asyncio.Queue,
):
    """
    Reads token chunks from the per-request asyncio.Queue (fed by the shared
    pubsub_listener) and forwards each as an SSE event to the client.

    On is_final=true: assembles the full answer and writes it to the semantic
    cache so future similar queries are served without RAG or GPU.

    Cleanup (unsubscribe + remove from _pending) always runs in finally,
    whether the stream finishes normally, times out, or the client disconnects.
    """
    chunks: list[str] = []
    try:
        while True:
            try:
                raw = await asyncio.wait_for(
                    queue.get(),
                    timeout=settings.RESULT_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                logger.warning(f"[{request_id}] GPU timed out after {settings.RESULT_TIMEOUT_SEC}s.")
                yield f"data: {json.dumps({'error': 'timeout'})}\n\n"
                return

            parsed = json.loads(raw)
            chunk = parsed.get("chunk", "")
            is_final = parsed.get("is_final", False)

            chunks.append(chunk)
            yield f"data: {json.dumps({'chunk': chunk, 'is_final': is_final})}\n\n"

            if is_final:
                full_answer = "".join(chunks)
                logger.info(f"[{request_id}] Stream complete ({len(chunks)} chunks). Writing to cache.")
                await step_cache_write(embedding, prompt, full_answer, request_id)
                yield "data: [DONE]\n\n"
                return

    finally:
        await _pubsub.unsubscribe(f"result:{request_id}")
        _pending.pop(request_id, None)
        logger.debug(f"[{request_id}] SSE stream cleaned up.")


# ── Route handlers ────────────────────────────────────────────────────────────

@app.post("/build-context")
async def handle_infer(body: InferRequest):
    """
    Main inference endpoint. Returns an SSE stream.

    Semaphore covers the fast compute path (embed → cache → RAG → dispatch)
    only. Once the job is enqueued the semaphore is released and the handler
    returns a StreamingResponse that forwards GPU tokens to the client as
    they arrive — holding the connection open without occupying a semaphore slot.
    """
    request_id = str(uuid.uuid4())
    total_start = time.perf_counter()

    async with _semaphore:
        try:
            logger.info(f"[{request_id}] Received: '{body.text[:60]}...'")

            # ── Step 1: Embed ──────────────────────────────────────────────
            embedding = await step_embed(body.text)

            # ── Step 2: Semantic cache lookup ──────────────────────────────
            cached_answer = await step_cache_lookup(embedding, request_id)
            if cached_answer is not None:
                return StreamingResponse(
                    sse_cache_hit(request_id, cached_answer),
                    media_type="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
                )

            # ── Step 3: RAG ────────────────────────────────────────────────
            context_chunks = await step_rag(embedding, request_id)

            # ── Step 4: Subscribe BEFORE dispatch so no early tokens missed ─
            queue: asyncio.Queue = asyncio.Queue()
            _pending[request_id] = queue
            await _pubsub.subscribe(f"result:{request_id}")

            # ── Step 5: Dispatch ───────────────────────────────────────────
            try:
                await step_dispatch(request_id, body.text, embedding, context_chunks)
            except Exception:
                await _pubsub.unsubscribe(f"result:{request_id}")
                _pending.pop(request_id, None)
                raise

            total_ms = (time.perf_counter() - total_start) * 1000
            logger.info(f"[{request_id}] Dispatched in {total_ms:.1f}ms | chunks={len(context_chunks)}")

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"[{request_id}] Unexpected error: {e}")
            raise HTTPException(status_code=500, detail="Internal worker error")

    # Semaphore released — GPU wait happens outside, connection stays open
    return StreamingResponse(
        sse_stream(request_id, embedding, body.text, queue),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


# @app.get("/metrics")
# async def get_metrics():
#     """Exposes latency percentiles and throughput stats."""
#     return _metrics.snapshot()
