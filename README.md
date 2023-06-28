# DeArrow Thumbnail Cache

This server acts as a cache for recently used generated thumbnails.

# Hosting yourself

The easiest way to host it yourself is using the docker images. Here is a sample compose file to get started. Make sure to copy `config.yaml.example` into `config.yaml`.

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

# Running Locally

`app.py` contains a web server where clients can request screenshots at specific timestamps. If it is not already generated, it can request generation through a redis queue.

To run the worker, run `worker.py`.

### License

AGPL-3.0
