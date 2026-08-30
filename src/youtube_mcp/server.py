import asyncio
from collections.abc import Awaitable
from contextlib import asynccontextmanager, suppress
from typing import Literal, TypeVar
from urllib.parse import urlparse

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ResourceLink, TextContent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Mount, Route
import uvicorn

from .api import YouTubeAPI
from .auth import CloudflareAccessMiddleware, CloudflareJWTVerifier
from .cache import ArtifactStore, MediaCache
from .config import Settings
from .downloader import YtDlpRunner
from .models import TranscriptionResult, TranscriptResult, VideoMetadata, VideoSearchResult
from .transcript import TranscriptFetcher
from .video import normalize_video
from .whisper import ModelChoice, WhisperModelManager, WhisperTranscriber


T = TypeVar("T")
SearchOrder = Literal["date", "rating", "relevance", "title", "viewCount"]
TranscriptOutput = Literal["text", "segments", "both"]


def _select_transcript_output(
    result: TranscriptResult | TranscriptionResult,
    output: TranscriptOutput,
) -> dict[str, object]:
    payload = result.model_dump(mode="json")
    if output == "text":
        payload.pop("segments", None)
    elif output == "segments":
        payload.pop("text", None)
    return payload


async def _run_long_operation(
    operation: Awaitable[T],
    context: Context,
    timeout_seconds: int,
    message: str,
) -> T:
    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(15)
            await context.report_progress(progress=0.5, total=1.0, message=message)

    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        async with asyncio.timeout(timeout_seconds):
            return await operation
    except TimeoutError as exc:
        raise ValueError("The operation exceeded the configured timeout.") from exc
    finally:
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task


def create_app(settings: Settings | None = None) -> Starlette:
    settings = settings or Settings.from_env()
    youtube = YouTubeAPI(settings.youtube_api_key)
    transcripts = TranscriptFetcher()
    cache = MediaCache(settings.cache_dir, settings.cache_ttl_seconds, settings.cache_max_bytes)
    downloader = YtDlpRunner(settings.long_operation_timeout_seconds)
    model_manager = WhisperModelManager(
        english_model=settings.whisper_en_model,
        multilingual_model=settings.whisper_multilingual_model,
        compute_type=settings.whisper_compute_type,
        cpu_threads=settings.whisper_cpu_threads,
        idle_seconds=settings.whisper_model_idle_seconds,
        beam_size=settings.whisper_beam_size,
        model_cache_dir=settings.model_cache_dir,
    )
    transcriber = WhisperTranscriber(
        model_manager,
        downloader,
        cache,
        settings.max_video_duration_seconds,
    )
    artifact_store = ArtifactStore(
        cache,
        downloader,
        settings.public_base_url,
        settings.cache_ttl_seconds,
        settings.artifact_max_bytes,
        settings.max_video_duration_seconds,
    )

    public_url = urlparse(settings.public_base_url)
    allowed_hosts = [
        public_url.netloc,
        "localhost",
        f"localhost:{settings.port}",
        "127.0.0.1",
        f"127.0.0.1:{settings.port}",
    ]
    mcp = FastMCP(
        "youtube",
        host=settings.host,
        port=settings.port,
        stateless_http=True,
        streamable_http_path="/mcp",
        transport_security=TransportSecuritySettings(
            allowed_hosts=list(dict.fromkeys(allowed_hosts)),
            allowed_origins=[settings.public_base_url],
        ),
    )

    @mcp.tool()
    async def get_video_details(video: str) -> VideoMetadata:
        """Get metadata for a YouTube video. Accepts a video ID or URL."""
        video_id, _ = normalize_video(video)
        return await asyncio.to_thread(youtube.get_video, video_id)

    @mcp.tool()
    async def get_transcript(
        video: str,
        language: str | None = None,
        output: TranscriptOutput = "text",
    ) -> dict[str, object]:
        """Get existing YouTube captions without using speech recognition.

        Select text, timestamped segments, or both with output.
        """
        video_id, _ = normalize_video(video)
        result = await asyncio.to_thread(transcripts.get_transcript, video_id, language)
        return _select_transcript_output(result, output)

    @mcp.tool()
    async def transcribe(
        video: str,
        context: Context,
        language: str | None = None,
        model: ModelChoice = "auto",
        output: TranscriptOutput = "text",
    ) -> dict[str, object]:
        """Explicitly download audio and run local speech recognition.

        This operation can take much longer than get_transcript. Use english or
        multilingual to override automatic model routing. Select text,
        timestamped segments, or both with output.
        """
        video_id, canonical_url = normalize_video(video)
        await context.report_progress(progress=0.0, total=1.0, message="Preparing audio")
        result = await _run_long_operation(
            transcriber.transcribe(video_id, canonical_url, language, model),
            context,
            settings.long_operation_timeout_seconds,
            "Local transcription is in progress",
        )
        await context.report_progress(progress=1.0, total=1.0, message="Transcription completed")
        return _select_transcript_output(result, output)

    @mcp.tool()
    async def search_videos(
        query: str,
        max_results: int = 10,
        language: str | None = None,
        order: SearchOrder = "relevance",
    ) -> list[VideoSearchResult]:
        """Search YouTube with the official YouTube Data API."""
        if not query.strip():
            raise ValueError("The search query must not be empty.")
        if not 1 <= max_results <= 50:
            raise ValueError("max_results must be from 1 through 50.")
        return await asyncio.to_thread(
            youtube.search_videos, query, max_results, language, order
        )

    @mcp.tool()
    async def materialize_video(
        video: str,
        context: Context,
        max_height: Literal[360, 480, 720] = 720,
    ) -> CallToolResult:
        """Create a downloadable MP4 artifact without analyzing its content."""
        video_id, canonical_url = normalize_video(video)
        await context.report_progress(progress=0.0, total=1.0, message="Preparing video")
        metadata = await _run_long_operation(
            artifact_store.materialize(video_id, canonical_url, max_height),
            context,
            settings.long_operation_timeout_seconds,
            "Video download is in progress",
        )
        await context.report_progress(progress=1.0, total=1.0, message="Video artifact is ready")
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=f"Download {metadata.filename}: {metadata.download_url}",
                ),
                ResourceLink(
                    type="resource_link",
                    uri=metadata.download_url,
                    name=metadata.filename,
                    mimeType=metadata.mime_type,
                    size=metadata.size,
                ),
            ],
            structuredContent=metadata.model_dump(mode="json"),
        )

    async def download_artifact(request: Request) -> Response:
        resolved = artifact_store.resolve(request.path_params["artifact_id"])
        if resolved is None:
            return Response("Artifact not found or expired.", status_code=404)
        metadata, path = resolved
        return FileResponse(
            path,
            media_type=metadata.mime_type,
            filename=metadata.filename,
            content_disposition_type="attachment",
        )

    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app: Starlette):
        cache.cleanup()
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            model_manager.unload()

    application = Starlette(
        routes=[
            Route("/artifacts/{artifact_id}", download_artifact, methods=["GET", "HEAD"]),
            Mount("/", app=mcp_app),
        ],
        lifespan=lifespan,
    )
    application.state.mcp = mcp
    application.state.artifact_store = artifact_store

    verifier = None
    if settings.auth_mode == "cloudflare":
        verifier = CloudflareJWTVerifier(settings.cf_issuer, settings.cf_access_audiences)
    application.add_middleware(CloudflareAccessMiddleware, verifier=verifier)
    return application


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
