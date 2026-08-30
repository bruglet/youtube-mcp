from datetime import datetime, timezone
import os
from pathlib import Path

import pytest

from youtube_mcp.cache import ArtifactStore, MediaCache


class FakeDownloader:
    async def probe(self, url):
        return {"duration": 120, "is_live": False, "title": "A test video"}

    async def download_video(self, url, output_template, max_height, max_bytes):
        path = output_template.with_name("video.mp4")
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
    assert datetime.fromisoformat(metadata.expires_at) > datetime.now(timezone.utc)


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
