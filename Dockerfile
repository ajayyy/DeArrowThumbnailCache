FROM python:3.11-alpine AS builder
RUN apk add --no-cache ffmpeg gcc musl-dev libffi-dev git
COPY requirements.txt /
RUN mkdir /wheels
WORKDIR /wheels
RUN pip wheel -r /requirements.txt

FROM python:3.11-alpine AS base
COPY --from=builder /wheels /wheels
RUN pip install /wheels/* && rm -rf /wheels
RUN apk add --no-cache ffmpeg curl
COPY . /app
WORKDIR /app

FROM base AS app
EXPOSE 3001
HEALTHCHECK CMD curl --no-progress-meter -fo /dev/null http://localhost:3001/api/v1/status || exit 1
CMD ["python", "app.py"]

FROM base AS worker
EXPOSE 3002
HEALTHCHECK CMD curl --no-progress-meter -fo /dev/null http://localhost:3002/ || exit 1
CMD ["python", "worker.py"]

