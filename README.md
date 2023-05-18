# DeArrow Thumbnail Cache

This server acts as a cache for recently used generated thumbnails.

`app.py` contains a web server where clients can request screenshots at specific timestamps. If it is not already generated, it can request generation through a redis queue.

To run the worker, run `worker.py`.

### License

AGPL-3.0