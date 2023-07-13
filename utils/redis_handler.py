from typing import Any, cast
from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.asyncio.client import PubSub
import time
from retry import retry
from rq.queue import Queue
from utils.config import config

redis_conn = Redis(host=config["redis"]["host"], port=config["redis"]["port"])
async_redis_conn: "AsyncRedis[str] | None" = None

queue_high = Queue("high", connection=redis_conn)
queue_low = Queue("default", connection=redis_conn)

async def init() -> None:
    await get_async_redis_conn()

@retry(tries=5, delay=0.1, backoff=3)
async def get_async_redis_conn() -> "AsyncRedis[str]":
    global async_redis_conn
    if async_redis_conn is not None:
        return async_redis_conn

    async_redis_conn = AsyncRedis(host=config["redis"]["host"], port=config["redis"]["port"])
    await async_redis_conn.ping()
    return async_redis_conn

def reset_async_redis_conn() -> None:
    global async_redis_conn
    async_redis_conn = None

async def get_redis_pubsub() -> PubSub:
    redis_conn = await get_async_redis_conn()
    redis_pubsub = redis_conn.pubsub(ignore_subscribe_messages=True)
    return redis_pubsub

@retry(tries=5, delay=0.1, backoff=3)
async def wait_for_message(key: str, timeout: int = 15) -> str:
    pubsub = None
    try:
        pubsub = await get_redis_pubsub()
        await pubsub.subscribe(key)

        start_time = time.time()
        while True:
            message = cast(dict[str, Any] | None, await pubsub.get_message(timeout=timeout))
            if message is not None:
                result = message["data"].decode()
                return result
            elif time.time() - start_time > timeout:
                raise TimeoutError("Timed out waiting for message")
    finally:
        if pubsub is not None:
            await pubsub.unsubscribe(key)
            await pubsub.close()
