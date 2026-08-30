import asyncio
import json
from collections.abc import Awaitable
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Literal, TypeVar
from urllib.parse import urlparse

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ResourceLink, TextContent, ToolAnnotations
from pydantic import Field
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
from .models import (
    ArtifactMetadata,
    TranscriptionResult,
    TranscriptionToolOutput,
    TranscriptResult,
    VideoMetadata,
    VideoSearchResult,
)
from .transcript import TranscriptFetcher
from .video import normalize_video
from .whisper import ModelChoice, WhisperModelManager, WhisperTranscriber


T = TypeVar("T")
SearchOrder = Literal["date", "rating", "relevance", "title", "viewCount"]
TranscriptOutput = Literal["text", "segments", "both"]

READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


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


def _transcript_call_result(
    result: TranscriptResult | TranscriptionResult,
    output: TranscriptOutput,
) -> CallToolResult:
    payload = _select_transcript_output(result, output)
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(payload, ensure_ascii=False, indent=2),
            )
        ],
        structuredContent=payload,
    )


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

    @mcp.tool(annotations=READ_ANNOTATIONS)
    async def get_video_details(
        video: Annotated[
            str,
            Field(
                description="A raw 11-character YouTube video ID or a YouTube URL. "
                "Use search_videos to find an ID when the user has not identified a video."
            ),
        ],
    ) -> VideoMetadata:
        (
            "Return metadata for one video, including its title, channel, description, "
            "duration, publication date, statistics, caption status, and thumbnails. "
            "Use when the user asks about a known video; use get_transcript for its "
            "spoken content and search_videos when no video is identified. Requires a "
            "configured YouTube API key."
        )
        video_id, _ = normalize_video(video)
        return await asyncio.to_thread(youtube.get_video, video_id)

    @mcp.tool(annotations=READ_ANNOTATIONS)
    async def get_transcript(
        video: Annotated[
            str,
            Field(
                description="A raw 11-character YouTube video ID or a YouTube URL. "
                "Use search_videos to find an ID when the user has not identified a video."
            ),
        ],
        language: Annotated[
            str | None,
            Field(
                description="A preferred YouTube caption language code, such as en or zh-Hans. "
                "Omit it to prefer a manual track, then English among equivalent tracks."
            ),
        ] = None,
        output: Annotated[
            TranscriptOutput,
            Field(
                description="The response form: text for compact plain text, segments for "
                "timestamps, or both only when both forms are needed."
            ),
        ] = "text",
    ) -> Annotated[CallToolResult, TranscriptResult]:
        (
            "Return YouTube-provided captions with language details as text, "
            "timestamped segments, or both. Use for summaries, quotations, or "
            "questions about spoken content; prefer this fast tool before transcribe, "
            "and use transcribe only when captions are unavailable or unsuitable. "
            "This tool never runs speech recognition and can return available=false."
        )
        video_id, _ = normalize_video(video)
        result = await asyncio.to_thread(transcripts.get_transcript, video_id, language)
        return _transcript_call_result(result, output)

    @mcp.tool(annotations=READ_ANNOTATIONS)
    async def transcribe(
        video: Annotated[
            str,
            Field(
                description="A raw 11-character YouTube video ID or a YouTube URL. "
                "Use search_videos to find an ID when the user has not identified a video."
            ),
        ],
        context: Context,
        language: Annotated[
            str | None,
            Field(
                description="An optional speech language hint, such as en, zh, or zh-CN. "
                "Omit it for automatic detection."
            ),
        ] = None,
        model: Annotated[
            ModelChoice,
            Field(
                description="The speech-recognition model family. Use auto normally, "
                "english for English-only speech, or multilingual for other languages."
            ),
        ] = "auto",
        output: Annotated[
            TranscriptOutput,
            Field(
                description="The response form: text for compact plain text, segments for "
                "timestamps, or both only when both forms are needed."
            ),
        ] = "text",
    ) -> Annotated[CallToolResult, TranscriptionToolOutput]:
        (
            "Return locally generated speech-to-text with language detection, model "
            "and cache information, and optional timestamps. Use when the user "
            "explicitly requests transcription or get_transcript reports that captions "
            "are unavailable or unsuitable; prefer get_transcript first because this "
            "tool can be much slower. Live or overlong videos are rejected, and the "
            "first call can download a model."
        )
        video_id, canonical_url = normalize_video(video)
        await context.report_progress(progress=0.0, total=1.0, message="Preparing audio")
        result = await _run_long_operation(
            transcriber.transcribe(video_id, canonical_url, language, model),
            context,
            settings.long_operation_timeout_seconds,
            "Local transcription is in progress",
        )
        await context.report_progress(progress=1.0, total=1.0, message="Transcription completed")
        return _transcript_call_result(result, output)

    @mcp.tool(annotations=READ_ANNOTATIONS)
    async def search_videos(
        query: Annotated[
            str,
            Field(
                description="Search terms based on the user request: a topic, title, "
                "channel, or keywords. For a known video ID or URL, use "
                "get_video_details instead."
            ),
        ],
        max_results: Annotated[
            int,
            Field(
                description="The number of videos to return, from 1 through 50. Keep this "
                "small unless the user requests a broad result set."
            ),
        ] = 10,
        language: Annotated[
            str | None,
            Field(
                description="An ISO 639-1 language code, such as en or zh, used to bias "
                "relevance. It does not restrict results to that language."
            ),
        ] = None,
        order: Annotated[
            SearchOrder,
            Field(
                description="The result order. Use relevance for general discovery, or "
                "select date, rating, title, or viewCount when the user requests it."
            ),
        ] = "relevance",
    ) -> list[VideoSearchResult]:
        (
            "Return matching videos with IDs, titles, channels, publication dates, "
            "descriptions, and thumbnails. Use when the user asks to find or discover "
            "videos, then pass a returned video_id to another tool as needed; do not "
            "use for details about one known video. Requires a configured YouTube API "
            "key."
        )
        if not query.strip():
            raise ValueError("The search query must not be empty.")
        if not 1 <= max_results <= 50:
            raise ValueError("max_results must be from 1 through 50.")
        return await asyncio.to_thread(
            youtube.search_videos, query, max_results, language, order
        )

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    async def materialize_video(
        video: Annotated[
            str,
            Field(
                description="A raw 11-character YouTube video ID or a YouTube URL. "
                "Use search_videos to find an ID when the user has not identified a video."
            ),
        ],
        context: Context,
        max_height: Annotated[
            Literal[360, 480, 720],
            Field(
                description="The maximum MP4 height in pixels. Use 720 unless the user "
                "requests a smaller download; source quality can reduce the actual height."
            ),
        ] = 720,
    ) -> Annotated[CallToolResult, ArtifactMetadata]:
        (
            "Return a temporary authenticated MP4 download link and file metadata. "
            "Use when the user asks to download or obtain the video file; do not use "
            "for metadata, captions, or speech transcription, and do not imply that "
            "the video was analyzed. Large, overlong, or live videos can be rejected."
        )
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
