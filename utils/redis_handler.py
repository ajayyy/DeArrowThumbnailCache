import asyncio
from typing import Any, cast
from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.asyncio.client import PubSub
import time
from utils.config import config

redis_conn = Redis(host=config["redis"]["host"], port=config["redis"]["port"]) # pyright: ignore[reportUnknownMemberType]
async_redis_conn: "AsyncRedis[str] | None" = None

async def init() -> None:
    await get_async_redis_conn()

async def get_async_redis_conn() -> "AsyncRedis[str]":
    global async_redis_conn
    if async_redis_conn is not None:
        return async_redis_conn

    async_redis_conn = AsyncRedis(host=config["redis"]["host"], port=config["redis"]["port"])
    await async_redis_conn.ping()
    return async_redis_conn

async def get_redis_pubsub() -> PubSub:
    redis_conn = await get_async_redis_conn()
    redis_pubsub = redis_conn.pubsub(ignore_subscribe_messages=True) # pyright: ignore[reportUnknownMemberType]
    return redis_pubsub

async def wait_for_message(key: str, timeout: int = 15) -> str:
    pubsub = await get_redis_pubsub()
    await pubsub.subscribe(key) # pyright: ignore[reportUnknownMemberType]

    start_time = time.time()
    while True:
        message = cast(dict[str, Any] | None, await pubsub.get_message(timeout=timeout)) # pyright: ignore[reportUnknownMemberType]
        if message is not None:
            return message["data"].decode()
        elif time.time() - start_time > timeout:
            raise TimeoutError("Timed out waiting for message")


asyncio.get_event_loop().run_in_executor(None, init)