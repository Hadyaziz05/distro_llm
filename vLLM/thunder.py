from vllm import AsyncLLMEngine, SamplingParams
from vllm.engine.arg_utils import AsyncEngineArgs
import redis.asyncio as aioredis
import asyncio, json
import socket
import os
import yaml
from dotenv import load_dotenv

CONSUMER_GROUP = "gpu_workers"
CONSUMER_NAME  = f"gpu-{socket.gethostname()}"  # unique per node

async def consumer_loop(engine, redis):
    # Create consumer group if it doesn't exist yet
    try:
        await redis.xgroup_create("inference_queue", CONSUMER_GROUP, id="0", mkstream=True)
    except Exception:
        pass  # group already exists

    while True:
        messages = await redis.xreadgroup(
            groupname=CONSUMER_GROUP,
            consumername=CONSUMER_NAME,
            streams={"inference_queue": ">"},  # ">" = only undelivered messages
            count=10,          # grab up to 10 jobs at once
            block=1000,        # wait 1s if queue is empty
        )
        if not messages:
            continue

        for _, msgs in messages:
            for msg_id, fields in msgs:
                job = json.loads(fields["data"])
                # Non-blocking — just adds to vLLM's internal waiting list
                asyncio.create_task(run_inference(engine, redis, msg_id, job))



async def run_inference(engine, redis, msg_id, job):
    request_id = job["request_id"]
    prompt     = job["prompt"]
    context    = job["context"]  # list of chunk dicts from RAG

    # Build the full prompt with context injected
    full_prompt = build_prompt(context, prompt)

    sampling_params = SamplingParams(max_tokens=1024)

    try:
        # engine.generate() is async and streams tokens as they are produced
        async for output in engine.generate(full_prompt, sampling_params, request_id):
            full_text = output.outputs[0].text
            is_final   = output.finished
            new_token = full_text[len(previous_text):]  # delta only
            previous_text = full_text
            await redis.publish(
                f"result:{request_id}",
                json.dumps({"chunk": new_token, "is_final": is_final}),
            )

            if is_final:
                break

        # ACK the input queue message — removes it from the pending list
        await redis.xack("inference_queue", CONSUMER_GROUP, msg_id)

    except Exception as e:
        # Publish an error event so the CPU worker doesn't hang waiting
        await redis.publish(
            f"result:{request_id}",
            json.dumps({"chunk": "", "is_final": True, "error": str(e)}),
        )
        # Do NOT ack — leave in queue for retry or dead-letter handling



def build_prompt(context_chunks, user_query):
    context_text = "\n\n".join(c["text"] for c in context_chunks)
    return (
        f"Answer using the following context:\n{context_text}\n\n"
        f"Question: {user_query}\nAnswer:"
    )


async def main():
    load_dotenv()
    
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis_password = os.getenv("REDIS_KEY", None)
    
    redis = await aioredis.from_url(redis_url, password=redis_password, decode_responses=True)

    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)

    # We map the YAML config to AsyncEngineArgs
    engine_args = AsyncEngineArgs(
        model=config.get("model", "meta-llama/Llama-3-8B-Instruct"),
        host=config.get("host", "0.0.0.0"),
        port=config.get("port", 30000),
        gpu_memory_utilization=config.get("gpu_memory_utilization", 0.90),
        max_model_len=config.get("max_model_len", 2048),
        max_num_seqs=config.get("max_num_seqs", 16),
        # max_num_batched_tokens is a legacy/different argument, but included if supported:
        # max_num_batched_tokens=config.get("max_num_batched_tokens", 2048), 
        enable_prefix_caching=config.get("enable_prefix_caching", False),
        enforce_eager=config.get("enforce_eager", False),
    )
    engine = AsyncLLMEngine.from_engine_args(engine_args)

    await consumer_loop(engine, redis)  # runs forever

asyncio.run(main())