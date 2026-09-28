FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim AS converter

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/*
RUN curl --fail --location --silent --show-error \
      https://github.com/arcusmaximus/YTSubConverter/releases/download/1.6.6/YTSubConverter-Linux.tar.xz \
      --output /tmp/ytsubconverter.tar.xz \
    && echo '66f25d482b79c32ff10d7ea5f6dc16c74facc283b59340a821d9d4f44952c7a9  /tmp/ytsubconverter.tar.xz' | sha256sum --check \
    && mkdir -p /opt/ytsubconverter \
    && tar -xJf /tmp/ytsubconverter.tar.xz -C /opt/ytsubconverter

FROM mcr.microsoft.com/dotnet/sdk:10.0-noble AS caption-converter

WORKDIR /src
COPY --from=converter /opt/ytsubconverter/YTSubConverter.Shared.dll /vendor/YTSubConverter.Shared.dll
COPY converter ./converter
RUN dotnet publish converter/HeadlessCaptionConverter.csproj -c Release -o /out

FROM mcr.microsoft.com/dotnet/runtime:10.0-noble

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates ffmpeg python3.12-venv libgomp1 libpangocairo-1.0-0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 youtube-mcp

COPY --from=builder /wheels /wheels
COPY --from=caption-converter /out /opt/caption-converter
COPY converter/YTSubConverter-LICENSE /opt/caption-converter/LICENSE
RUN python3.12 -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels

ENV PATH=/opt/venv/bin:$PATH \
    HOST=0.0.0.0 \
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

ENTRYPOINT ["/opt/venv/bin/youtube-mcp"]
