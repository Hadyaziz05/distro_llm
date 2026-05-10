"""
config.py — All configuration via environment variables.
Set these in your .env file or deployment environment.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from the current directory FIRST
env_file = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_file, override=True)


@dataclass
class Settings:

    # ── Embedding ─────────────────────────────────────────────────────────────
    EMBEDDING_MODEL: str = field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2"))
    # Number of threads for embedding. Rule: match physical CPU cores.
    # More threads = more GIL contention, not more speed.
    THREAD_POOL_SIZE: int = field(default_factory=lambda: int(os.getenv("THREAD_POOL_SIZE", os.cpu_count() or 4)))

    # ── Qdrant ────────────────────────────────────────────────────────────────
    # Cloud-based Qdrant (from .env)
    QDRANT_URL: str = field(default_factory=lambda: os.getenv("QDRANT_URL", ""))
    QDRANT_API_KEY: str = field(default_factory=lambda: os.getenv("QDRANT_API_KEY", ""))
    # Local Qdrant (fallback)
    QDRANT_HOST: str = field(default_factory=lambda: os.getenv("QDRANT_HOST", "localhost"))
    QDRANT_PORT: int = field(default_factory=lambda: int(os.getenv("QDRANT_PORT", "6333")))
    QDRANT_COLLECTION: str = field(default_factory=lambda: os.getenv("QDRANT_COLLECTION", "my_docs"))
    QDRANT_TIMEOUT_SEC: float = field(default_factory=lambda: float(os.getenv("QDRANT_TIMEOUT_SEC", "5.0")))
    # Connection pool: tune based on expected concurrent Qdrant queries per process
    QDRANT_MAX_CONNECTIONS: int = field(default_factory=lambda: int(os.getenv("QDRANT_MAX_CONNECTIONS", "100")))
    QDRANT_KEEPALIVE_CONNECTIONS: int = field(default_factory=lambda: int(os.getenv("QDRANT_KEEPALIVE_CONNECTIONS", "50")))

    # ── RAG ───────────────────────────────────────────────────────────────────
    RAG_TOP_K: int = field(default_factory=lambda: int(os.getenv("RAG_TOP_K", "5")))
    # Only return chunks above this similarity score (0.0–1.0)
    RAG_SCORE_THRESHOLD: float = field(default_factory=lambda: float(os.getenv("RAG_SCORE_THRESHOLD", "0.6")))

    # ── Semantic Cache ────────────────────────────────────────────────────────
    CACHE_COLLECTION: str = field(default_factory=lambda: os.getenv("CACHE_COLLECTION", "semantic_cache"))
    # Must be well above RAG_SCORE_THRESHOLD to avoid false cache hits
    CACHE_SIMILARITY_THRESHOLD: float = field(default_factory=lambda: float(os.getenv("CACHE_SIMILARITY_THRESHOLD", "0.92")))

    # ── Redis ─────────────────────────────────────────────────────────────────
    REDIS_URL: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379"))
    REDIS_STREAM_KEY: str = field(default_factory=lambda: os.getenv("REDIS_STREAM_KEY", "inference_queue"))
    # Cap stream length to bound memory usage on Redis
    REDIS_STREAM_MAXLEN: int = field(default_factory=lambda: int(os.getenv("REDIS_STREAM_MAXLEN", "10000")))
    REDIS_MAX_CONNECTIONS: int = field(default_factory=lambda: int(os.getenv("REDIS_MAX_CONNECTIONS", "100")))
    # Separate client for the shared Pub/Sub connection that receives GPU result
    # tokens. All 1000+ concurrent requests are multiplexed over one connection;
    # a small pool is enough.
    REDIS_READER_MAX_CONNECTIONS: int = field(default_factory=lambda: int(os.getenv("REDIS_READER_MAX_CONNECTIONS", "10")))
    RESULT_TIMEOUT_SEC: int = field(default_factory=lambda: int(os.getenv("RESULT_TIMEOUT_SEC", "60")))
    # ── Concurrency ───────────────────────────────────────────────────────────
    # Semaphore limit per process. With 4 processes: 4 × 250 = 1000 total.
    MAX_CONCURRENT_PER_PROCESS: int = field(default_factory=lambda: int(os.getenv("MAX_CONCURRENT_PER_PROCESS", "250")))

    # ── Server ────────────────────────────────────────────────────────────────
    HOST: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    PORT: int = field(default_factory=lambda: int(os.getenv("PORT", "8000")))
    # Number of uvicorn worker processes. Rule: match CPU cores.
    UVICORN_WORKERS: int = field(default_factory=lambda: int(os.getenv("UVICORN_WORKERS", "1")))


settings = Settings()

