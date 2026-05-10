"""
models.py — Request/response schemas.
"""

from typing import Optional
from pydantic import BaseModel, Field


class InferRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=8192, description="Input text to embed and process")
    top_k: Optional[int] = Field(None, ge=1, le=20, description="Override RAG top-k for this request")


class InferResponse(BaseModel):
    request_id: str
    status: str
    context_chunks: int
    latency_ms: float
    worker_pid: int


class HealthResponse(BaseModel):
    status: str
    pid: int
    qdrant_ok: bool
    redis_ok: bool
    active_requests: int
    metrics: dict
