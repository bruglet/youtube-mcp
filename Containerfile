FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 youtube-mcp

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels

ENV HOST=0.0.0.0 \
    PORT=8000 \
    CACHE_DIR=/data/cache \
    MODEL_CACHE_DIR=/data/models \
    HF_HOME=/data/models/huggingface \
    XDG_CACHE_HOME=/data/cache/runtime \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN mkdir -p /data/cache /data/models \
    && chown -R youtube-mcp:youtube-mcp /data

USER youtube-mcp
EXPOSE 8000
VOLUME ["/data/cache", "/data/models"]

ENTRYPOINT ["youtube-mcp"]
