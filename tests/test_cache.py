from datetime import datetime, timezone
import os
from pathlib import Path

import pytest

from youtube_mcp.cache import ArtifactStore, MediaCache
from youtube_mcp.models import TranscriptionResult, WhisperSegment


class FakeDownloader:
    duration = 120
    calls = 0
    subtitles = None
    automatic_captions = None

    async def probe(self, url):
        return {
            "duration": self.duration,
            "is_live": False,
            "title": "A test video",
            "subtitles": self.subtitles,
            "automatic_captions": self.automatic_captions,
        }

    async def download_video(
        self, url, output_template, max_height, max_bytes, max_fps=None,
        dynamic_range="auto", caption_tracks=None,
    ):
        self.calls += 1
        self.caption_tracks = caption_tracks
        advanced = max_height > 720 or max_fps is not None or dynamic_range != "auto"
        path = output_template.with_name("video.mkv" if advanced else "video.mp4")
        path.write_bytes(b"video-data")
        return path


@pytest.mark.asyncio
async def test_artifact_survives_store_recreation(tmp_path):
    cache = MediaCache(tmp_path, ttl_seconds=3600, max_bytes=1024)
    store = ArtifactStore(
        cache,
        FakeDownloader(),
        "https://mcp.example.com",
        ttl_seconds=3600,
        max_artifact_bytes=512,
        max_duration_seconds=3600,
    )

    metadata = await store.materialize(
        "dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", 720
    )
    recreated = ArtifactStore(
        MediaCache(tmp_path, 3600, 1024),
        FakeDownloader(),
        "https://mcp.example.com",
        3600,
        512,
        3600,
    )
    resolved = recreated.resolve(metadata.artifact_id)

    assert resolved is not None
    assert resolved[0].filename == "A-test-video.mp4"
    assert resolved[0].max_height == 720
    assert resolved[1].read_bytes() == b"video-data"
    assert metadata.download_url.startswith("https://mcp.example.com/artifacts/")
    assert metadata.cached is False

    reused = await recreated.materialize(
        "dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", 720
    )
    assert reused.cached is True
    assert datetime.fromisoformat(metadata.expires_at) > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_materialize_requires_explicit_duration_override(tmp_path):
    cache = MediaCache(tmp_path, ttl_seconds=3600, max_bytes=1024)
    downloader = FakeDownloader()
    downloader.duration = 3601
    store = ArtifactStore(
        cache,
        downloader,
        "https://mcp.example.com",
        ttl_seconds=3600,
        max_artifact_bytes=512,
        max_duration_seconds=3600,
    )

    with pytest.raises(ValueError, match="duration limit"):
        await store.materialize(
            "dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", 720
        )

    result = await store.materialize(
        "dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        720,
        override_duration_limit=True,
    )

    assert result.filename == "A-test-video.mp4"


@pytest.mark.asyncio
async def test_custom_quality_has_separate_cached_mkv(tmp_path):
    cache = MediaCache(tmp_path, ttl_seconds=3600, max_bytes=1024)
    downloader = FakeDownloader()
    store = ArtifactStore(cache, downloader, "https://mcp.example.com", 3600, 512, 3600)
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    regular = await store.materialize("dQw4w9WgXcQ", url, 720)
    custom = await store.materialize("dQw4w9WgXcQ", url, 2160, max_fps=30, dynamic_range="hdr")
    reused = await store.materialize("dQw4w9WgXcQ", url, 2160, max_fps=30, dynamic_range="hdr")

    assert regular.filename.endswith(".mp4")
    assert custom.filename.endswith(".mkv")
    assert custom.mime_type == "video/x-matroska"
    assert custom.max_fps == 30
    assert custom.dynamic_range == "hdr"
    assert custom.artifact_id != regular.artifact_id
    assert reused.cached is True
    assert downloader.calls == 2
    assert store.resolve(custom.artifact_id)[1].suffix == ".mkv"


@pytest.mark.asyncio
async def test_materialization_embeds_source_captions_and_caches_language_choice(tmp_path):
    downloader = FakeDownloader()
    downloader.subtitles = {"en": [{"url": "https://example.com/manual"}]}
    downloader.automatic_captions = {
        "en": [{"url": "https://example.com/auto?lang=en", "name": "English"}],
        "es": [{"url": "https://example.com/auto?lang=es", "name": "Spanish"}],
        "fr": [{"url": "https://example.com/auto?lang=es&tlang=fr", "name": "French"}],
    }
    store = ArtifactStore(MediaCache(tmp_path, 3600, 1024), downloader, "https://mcp.example.com", 3600, 512, 3600)
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    default = await store.materialize("dQw4w9WgXcQ", url, 720)
    assert [(track.language_code, track.source) for track in downloader.caption_tracks] == [
        ("en", "manual"), ("es", "automatic")
    ]
    spanish = await store.materialize("dQw4w9WgXcQ", url, 720, caption_language="es")
    assert [(track.language_code, track.source) for track in downloader.caption_tracks] == [
        ("es", "automatic")
    ]
    assert spanish.artifact_id != default.artifact_id
    assert (await store.materialize("dQw4w9WgXcQ", url, 720, caption_language="es")).cached
    assert downloader.calls == 2


def test_transcription_result_survives_cache_recreation(tmp_path):
    result = TranscriptionResult(
        video_id="dQw4w9WgXcQ",
        canonical_url="https://example.com",
        text="hello",
        segments=[WhisperSegment(start=0.0, end=1.0, text="hello")],
        language_code="en",
        language_probability=0.99,
        model="small.en",
    )
    cache = MediaCache(tmp_path, ttl_seconds=3600, max_bytes=1024)
    cache.store_transcription("result-key", result)

    recreated = MediaCache(tmp_path, ttl_seconds=3600, max_bytes=1024)
    cached = recreated.cached_transcription("result-key")

    assert cached == result


def test_cache_removes_expired_audio(tmp_path):
    cache = MediaCache(tmp_path, ttl_seconds=10, max_bytes=1024)
    audio = cache.audio_dir / "old.webm"
    audio.write_bytes(b"old")
    os.utime(audio, (0, 0))

    cache.cleanup()

    assert not audio.exists()


def test_cache_evicts_oldest_media(tmp_path):
    cache = MediaCache(tmp_path, ttl_seconds=3600, max_bytes=5)
    first = cache.audio_dir / "first.webm"
    second = cache.audio_dir / "second.webm"
    first.write_bytes(b"1234")
    second.write_bytes(b"5678")
    os.utime(first, (1, 1))

    cache.cleanup()

    assert not first.exists()
    assert second.exists()
