"""
main.py — Entrypoint. Launches uvicorn with N worker processes.

Each process gets its own:
  - Event loop
  - ThreadPoolExecutor
  - Embedding model (loaded once, in memory)
  - Qdrant + Redis connection pools

Run: python main.py
Or:  uvicorn worker:app --workers 4 --host 0.0.0.0 --port 8000
"""

import os
import uvicorn
from worker.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "worker:app",
        host=settings.HOST,
        port=settings.PORT,
        workers=settings.UVICORN_WORKERS,  # = CPU cores
        loop="uvloop",      # faster event loop implementation (drop-in for asyncio)
        http="httptools",   # faster HTTP parser
        log_level="info",
        access_log=True,
        # Timeouts
        timeout_keep_alive=30,
    )
