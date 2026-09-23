# DeArrow Thumbnail Cache
This server acts as a cache for recently used generated thumbnails.

## Configuration options
Make sure to copy `config.yaml.example` into `config.yaml`. You can eventually configure as you want by parameters modification.

### Config parameters
* `thumbnail_storage.max_cache` lets you configure cache size and control container's volume size. 

## Hosting yourself
The easiest way to host it yourself is starting a service by using Docker containers. Here is a sample compose file to get started.

```yaml
version: '3'
name: thumbnail-generator
services:
  redis:
    container_name: redis
    image: redis:7.0
    command: /usr/local/etc/redis/redis.conf
    volumes:
      - ./redis/redis.conf:/usr/local/etc/redis/redis.conf
    ports:
      - 32774:6379
    sysctls:
      - net.core.somaxconn=324000
      - net.ipv4.tcp_max_syn_backlog=3240000
    restart: always
  app:
    container_name: app
    image: ghcr.io/ajayyy/thumbnail-cache
    ports:
      - 3001:3001
    volumes:
      - cache:/app/cache
      - ./config.yaml:/app/config.yaml
    restart: always
  worker:
    container_name: worker
    image: ghcr.io/ajayyy/thumbnail-cache-worker
    volumes:
      - cache:/app/cache
      - ./config.yaml:/app/config.yaml
    restart: always

volumes:
  cache:
    external: true
```

## Running Locally

By running `app.py` Python script, you start a web server where clients can request screenshots at specific timestamps. If it is not already generated, it can request generation through a Redis queue.

To run the worker, run `python worker.py`.

## License

AGPL-3.0
