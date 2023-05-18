from redis import Redis
from rq.worker import SimpleWorker as Worker
from utils.config import config

redis_conn = Redis(host=config["redis"]["host"], port=config["redis"]["port"])

listen = ["high", "default"]

worker = Worker(listen, connection=redis_conn)
worker.work()