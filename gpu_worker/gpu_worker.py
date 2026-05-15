import asyncio
import json
import socket
import os
import yaml
import logging
from dotenv import load_dotenv

# vLLM Imports
from vllm import AsyncLLMEngine, SamplingParams
from vllm.engine.arg_utils import AsyncEngineArgs

# Redis Import
import redis.asyncio as aioredis

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gpu-worker")

CONSUMER_GROUP = "gpu_workers"
CONSUMER_NAME = f"gpu-{socket.gethostname()}"
STREAM_NAME = "inference_queue"

def build_prompt(context_chunks, user_query):
    """Manually applies Llama 3 instruct chat template."""
    context_text = "\n\n".join(c.get("text", "") for c in context_chunks)

    system_content = (
        "You are a helpful assistant. Answer questions clearly and concisely "
        "based on the provided context. If the context does not contain enough "
        "information, say so."
    )

    if context_text:
        user_content = f"Context:\n{context_text}\n\nQuestion: {user_query}"
    else:
        user_content = user_query

    return (
        "<|begin_of_text|>"
        "<|start_header_id|>system<|end_header_id|>\n\n"
        f"{system_content}"
        "<|eot_id|>"
        "<|start_header_id|>user<|end_header_id|>\n\n"
        f"{user_content}"
        "<|eot_id|>"
        "<|start_header_id|>assistant<|end_header_id|>\n\n"
    )

async def run_inference(engine, redis, msg_id, job):
    request_id = job.get("request_id")
    prompt      = job.get("prompt")
    context     = job.get("context", [])

    full_prompt = build_prompt(context, prompt)

    sampling_params = SamplingParams(
        max_tokens=job.get("max_tokens", 1024),
        temperature=job.get("temperature", 0.7),
        top_p=job.get("top_p", 0.95),
        stop=["<|eot_id|>", "<|end_of_text|>"],
    )

    previous_text = ""
    try:
        results_generator = engine.generate(
            full_prompt,
            sampling_params,
            request_id,
        )

        async for output in results_generator:
            full_text = output.outputs[0].text
            is_final  = output.finished

            new_token = full_text[len(previous_text):]
            previous_text = full_text

            logger.info(f"Streaming result for {request_id}: {repr(new_token)}")

            await redis.publish(
                f"result:{request_id}",
                json.dumps({
                    "chunk":      new_token,
                    "is_final":   is_final,
                    "request_id": request_id,
                }),
            )

        await redis.xack(STREAM_NAME, CONSUMER_GROUP, msg_id)
        logger.info(f"Completed request: {request_id}")

    except Exception as e:
        logger.error(f"Inference error for {request_id}: {e}")
        await redis.publish(
            f"result:{request_id}",
            json.dumps({"chunk": "", "is_final": True, "error": str(e), "request_id": request_id}),
        )
        # Not ACKing so the message can be retried or moved to a DLQ

async def consumer_loop(engine, redis):
    try:
        await redis.xgroup_create(STREAM_NAME, CONSUMER_GROUP, id="0", mkstream=True)
        logger.info(f"Created consumer group '{CONSUMER_GROUP}' on stream '{STREAM_NAME}'")
    except Exception:
        logger.info(f"Consumer group '{CONSUMER_GROUP}' already exists, continuing...")

    logger.info(f"Worker '{CONSUMER_NAME}' listening for jobs...")

    while True:
        try:
            messages = await redis.xreadgroup(
                groupname=CONSUMER_GROUP,
                consumername=CONSUMER_NAME,
                streams={STREAM_NAME: ">"},
                count=5,
                block=1000,
            )

            if not messages:
                await asyncio.sleep(0.1)
                continue

            for _, msgs in messages:
                for msg_id, fields in msgs:
                    try:
                        job = json.loads(fields["data"])
                        asyncio.create_task(run_inference(engine, redis, msg_id, job))
                    except Exception as e:
                        logger.error(f"Failed to parse job {msg_id}: {e}")
                        await redis.xack(STREAM_NAME, CONSUMER_GROUP, msg_id)

        except Exception as e:
            logger.error(f"Consumer loop error: {e}")
            await asyncio.sleep(5)

async def main():
    load_dotenv()

    redis_url      = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis_password = os.getenv("REDIS_KEY", None)

    redis = await aioredis.from_url(
        redis_url,
        password=redis_password,
        decode_responses=True,
    )

    config_path = "config.yaml"
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
    else:
        config = {}
        logger.warning("config.yaml not found, using defaults.")

    engine_args = AsyncEngineArgs(
        model=config.get("model", "meta-llama/Llama-3-8B-Instruct"),
        gpu_memory_utilization=config.get("gpu_memory_utilization", 0.90),
        max_model_len=config.get("max_model_len", 2048),
        max_num_seqs=config.get("max_num_seqs", 16),
        enable_prefix_caching=config.get("enable_prefix_caching", True),
        enforce_eager=config.get("enforce_eager", False),
        trust_remote_code=True,
    )

    engine = AsyncLLMEngine.from_engine_args(engine_args)

    await consumer_loop(engine, redis)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker shutting down...")