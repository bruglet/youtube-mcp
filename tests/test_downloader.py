from unittest.mock import AsyncMock
from pathlib import Path

import pytest

from youtube_mcp.downloader import YtDlpRunner, source_caption_tracks, video_formats
from youtube_mcp.models import CaptionOption


def test_video_formats_groups_qualities_and_codecs():
    info = {
        "formats": [
            {"height": 1080, "fps": 60, "dynamic_range": "SDR", "vcodec": "av01.0", "acodec": "none"},
            {"height": 1080, "fps": 60, "dynamic_range": "SDR", "vcodec": "vp9", "acodec": "none"},
            {"height": 2160, "fps": 30, "dynamic_range": "HDR10", "vcodec": "vp9.2", "acodec": "none"},
            {"vcodec": "none", "acodec": "opus"},
            {"vcodec": "none", "acodec": "mp4a.40.2"},
        ],
        "subtitles": {"en": [{"url": "https://example.com/manual"}]},
    }

    result = video_formats("dQw4w9WgXcQ", "https://youtube.com/watch?v=dQw4w9WgXcQ", info)

    assert [(option.height, option.fps, option.dynamic_range) for option in result.options] == [
        (2160, 30, "HDR10"),
        (1080, 60, "SDR"),
    ]
    assert result.options[1].video_codecs == ["AV1", "VP9"]
    assert result.audio_codecs == ["Opus", "AAC"]
    assert result.caption_tracks == [CaptionOption(language_code="en", source="manual")]


def test_caption_tracks_exclude_translations_and_prefer_manual():
    info = {
        "subtitles": {
            "en": [{"url": "https://example.com/manual"}],
            "live_chat": [{"url": "https://example.com/chat"}],
        },
        "automatic_captions": {
            "en": [{"url": "https://example.com/auto?lang=en", "name": "English"}],
            "en-US": [{"url": "https://example.com/auto?lang=en-US", "name": "English (US)"}],
            "es": [{"url": "https://example.com/auto?lang=es", "name": "Spanish"}],
            "es-orig": [{"url": "https://example.com/auto?lang=es", "name": "Spanish (Original)"}],
            "fr": [{"url": "https://example.com/auto?lang=es&tlang=fr", "name": "French"}],
            "de-en": [{"url": "https://example.com/manual?lang=en", "name": "German from English"}],
        },
    }

    assert source_caption_tracks(info) == [
        CaptionOption(language_code="en", source="manual"),
        CaptionOption(language_code="en-US", source="automatic"),
        CaptionOption(language_code="es", source="automatic"),
    ]
    assert source_caption_tracks(info, "es") == [
        CaptionOption(language_code="es", source="automatic")
    ]
    assert source_caption_tracks(info, "en-US") == [
        CaptionOption(language_code="en-US", source="automatic")
    ]
    assert source_caption_tracks(info, "fr") == []

    info["subtitles"] = {"en-US": [{"url": "https://example.com/manual-us"}]}
    assert source_caption_tracks(info, "en") == [
        CaptionOption(language_code="en-US", source="manual")
    ]


@pytest.mark.asyncio
async def test_download_video_quality_selector_and_container(tmp_path):
    runner = YtDlpRunner(30)
    runner._run = AsyncMock(return_value="")
    template = tmp_path / "video.%(ext)s"

    (tmp_path / "video.mp4").write_bytes(b"video")
    await runner.download_video("https://youtube.com/watch?v=dQw4w9WgXcQ", template, 720, 100)
    args = runner._run.await_args.args
    assert "bv*[height<=720]+ba/b[height<=720]" in args
    assert args[args.index("--merge-output-format") + 1] == "mp4"

    (tmp_path / "video.mp4").unlink()
    (tmp_path / "video.mkv").write_bytes(b"video")
    await runner.download_video(
        "https://youtube.com/watch?v=dQw4w9WgXcQ", template, 2160, 100, 30, "hdr"
    )
    args = runner._run.await_args.args
    assert "bv*[height<=2160][fps<=30][dynamic_range!=SDR]+ba/b[height<=2160][fps<=30][dynamic_range!=SDR]" in args
    assert args[args.index("--merge-output-format") + 1] == "mkv"
    assert args[args.index("--format-sort") + 1] == "res,fps,hdr:12,vcodec,acodec"


@pytest.mark.asyncio
async def test_download_embeds_selected_caption_tracks_in_one_container(tmp_path):
    runner = YtDlpRunner(30)
    runner._run = AsyncMock(return_value="")
    (tmp_path / "video.mp4").write_bytes(b"video")
    (tmp_path / "video.en.vtt").write_bytes(b"captions longer than video")
    tracks = [
        CaptionOption(language_code="en", source="manual"),
        CaptionOption(language_code="es", source="automatic"),
    ]

    path = await runner.download_video(
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        tmp_path / "video.%(ext)s",
        720,
        100,
        caption_tracks=tracks,
    )

    args = runner._run.await_args.args
    assert path.suffix == ".mp4"
    assert args[args.index("--sub-langs") + 1] == "^en$,^es$"
    assert "--embed-subs" in args
    assert "--write-subs" in args
    assert "--write-auto-subs" in args
    assert "--sleep-subtitles" not in args

    await runner.download_video(
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        tmp_path / "video.%(ext)s",
        720,
        100,
        caption_tracks=tracks + [CaptionOption(language_code="ja", source="manual")],
    )
    args = runner._run.await_args.args
    assert args[args.index("--sleep-subtitles") + 1] == "5"


@pytest.mark.asyncio
async def test_mkv_converts_srv3_and_vtt_to_ass_before_muxing(tmp_path):
    runner = YtDlpRunner(30)
    runner._run = AsyncMock(return_value="")
    calls = []

    async def fake_process(label, *args):
        calls.append((label, args))
        if label == "YTSubConverter":
            Path(args[3]).write_text("converted ASS")
        elif label == "ffmpeg":
            Path(args[-1]).write_bytes(b"muxed video" if args[-1].endswith(".mkv") else b"ASS")
        return ""

    runner._run_process = fake_process
    (tmp_path / "video.mkv").write_bytes(b"video")
    (tmp_path / "video.en.srv3").write_text("styled captions")
    (tmp_path / "video.es.vtt").write_text("plain captions")
    tracks = [
        CaptionOption(language_code="en", source="manual"),
        CaptionOption(language_code="es", source="automatic"),
    ]

    path = await runner.download_video(
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        tmp_path / "video.%(ext)s", 1080, 100,
        caption_tracks=tracks,
    )

    args = runner._run.await_args.args
    assert args[args.index("--sub-format") + 1] == "srv3/vtt"
    assert "--embed-subs" not in args
    assert calls[0][0] == "YTSubConverter"
    assert calls[0][1][:2] == ("dotnet", "/opt/caption-converter/HeadlessCaptionConverter.dll")
    assert calls[1][0] == "ffmpeg" and calls[1][1][-1].endswith(".ass")
    assert calls[2][0] == "ffmpeg" and "-map" in calls[2][1]
    assert path.read_bytes() == b"muxed video"
