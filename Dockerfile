FROM python:3.11-alpine AS builder
RUN apk add --no-cache ffmpeg gcc musl-dev libffi-dev
COPY requirements.txt /
RUN mkdir /wheels
WORKDIR /wheels
RUN pip wheel -r /requirements.txt

FROM python:3.11-alpine AS base
COPY --from=builder /wheels /wheels
RUN pip install /wheels/* && rm -rf /wheels
RUN apk add --no-cache ffmpeg
COPY . /app
WORKDIR /app

FROM base AS app
EXPOSE 3000
CMD ["python", "app.py"]

FROM base AS worker
EXPOSE 3001
CMD ["python", "worker.py"]

