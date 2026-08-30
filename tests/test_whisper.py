from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from youtube_mcp.models import TranscriptionResult
from youtube_mcp.whisper import WhisperModelManager, WhisperTranscriber


def _manager(tmp_path):
    return WhisperModelManager(
        english_model="small.en",
        multilingual_model="small",
        compute_type="int8",
        cpu_threads=4,
        idle_seconds=3600,
        beam_size=5,
        model_cache_dir=tmp_path,
    )


def test_model_routing():
    assert WhisperModelManager._select_family(None, "auto") == "english"
    assert WhisperModelManager._select_family("en-US", "auto") == "english"
    assert WhisperModelManager._select_family("zh", "auto") == "multilingual"
    assert WhisperModelManager._select_family(None, "multilingual") == "multilingual"


def test_manager_preserves_timestamps_and_language(tmp_path):
    manager = _manager(tmp_path)
    model = MagicMock()
    model.transcribe.return_value = (
        iter([SimpleNamespace(start=0.0, end=2.5, text=" Hello world ")]),
        SimpleNamespace(language="en", language_probability=0.99),
    )
    manager._model = model
    manager._loaded_name = "small.en"

    result = manager.transcribe(
        Path("audio.webm"), None, "auto", "dQw4w9WgXcQ", "https://example.com"
    )
    manager.unload()

    assert result.text == "Hello world"
    assert result.segments[0].end == 2.5
    assert result.language_code == "en"
    assert result.language_probability == 0.99
    assert result.model == "small.en"
    assert model.transcribe.call_args.kwargs["vad_filter"] is True


@pytest.mark.asyncio
async def test_transcriber_reuses_cached_audio(tmp_path, mocker):
    manager = MagicMock()
    manager.transcribe.return_value = TranscriptionResult(
        video_id="dQw4w9WgXcQ",
        canonical_url="https://example.com",
        text="text",
        segments=[],
        model="small.en",
    )
    downloader = MagicMock()
    downloader.probe = MagicMock(return_value={"duration": 60, "is_live": False})

    async def probe(url):
        return {"duration": 60, "is_live": False}

    downloader.probe = probe
    cache = MagicMock()
    cache.cached_transcription.return_value = None
    cache.cached_audio.return_value = tmp_path / "audio.webm"
    transcriber = WhisperTranscriber(manager, downloader, cache, 3600)

    async def run_inline(function, *arguments):
        return function(*arguments)

    mocker.patch("youtube_mcp.whisper.asyncio.to_thread", side_effect=run_inline)

    result = await transcriber.transcribe(
        "dQw4w9WgXcQ", "https://example.com", None, "auto"
    )

    assert result.text == "text"
    assert result.cached is False
    downloader.download_audio.assert_not_called()
    manager.transcribe.assert_called_once()
    cache.store_transcription.assert_called_once_with(
        manager.result_cache_key.return_value, result
    )


@pytest.mark.asyncio
async def test_transcriber_reuses_cached_result():
    cached = TranscriptionResult(
        video_id="dQw4w9WgXcQ",
        canonical_url="https://example.com",
        text="cached",
        segments=[],
        model="small.en",
    )
    manager = MagicMock()
    manager.result_cache_key.return_value = "result-key"
    downloader = MagicMock()
    cache = MagicMock()
    cache.cached_transcription.return_value = cached
    transcriber = WhisperTranscriber(manager, downloader, cache, 3600)

    result = await transcriber.transcribe(
        "dQw4w9WgXcQ", "https://example.com", None, "auto"
    )

    assert result.model_dump(exclude={"cached"}) == cached.model_dump(exclude={"cached"})
    assert cached.cached is False
    assert result.cached is True
    downloader.probe.assert_not_called()
    manager.transcribe.assert_not_called()


@pytest.mark.asyncio
async def test_transcriber_rejects_long_video():
    downloader = MagicMock()

    async def probe(url):
        return {"duration": 3601, "is_live": False}

    downloader.probe = probe
    manager = MagicMock()
    cache = MagicMock()
    cache.cached_transcription.return_value = None
    transcriber = WhisperTranscriber(manager, downloader, cache, 3600)
    with pytest.raises(ValueError, match="duration limit"):
        await transcriber.transcribe(
            "dQw4w9WgXcQ", "https://example.com", None, "auto"
        )
