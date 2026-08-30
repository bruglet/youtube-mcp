from types import SimpleNamespace

from youtube_transcript_api._errors import TranscriptsDisabled

from youtube_mcp.transcript import TranscriptFetcher


def _snippet(start, duration, text):
    return SimpleNamespace(start=start, duration=duration, text=text)


def _track(code, generated, text):
    return SimpleNamespace(
        language_code=code,
        language={"en": "English", "zh": "Chinese"}.get(code, code),
        is_generated=generated,
        is_translatable=True,
        fetch=lambda: [_snippet(0.0, 2.5, text)],
    )


def test_prefers_english_manual_track(mocker):
    fetcher = TranscriptFetcher()
    tracks = [
        _track("zh", False, "manual Chinese"),
        _track("en", True, "generated English"),
        _track("en", False, "manual English"),
    ]
    mocker.patch.object(fetcher._api, "list", return_value=tracks)

    result = fetcher.get_transcript("dQw4w9WgXcQ")

    assert result.available is True
    assert result.language_code == "en"
    assert result.is_generated is False
    assert result.text == "manual English"
    assert result.segments[0].end == 2.5


def test_requested_language_prefers_manual(mocker):
    fetcher = TranscriptFetcher()
    tracks = [_track("zh", True, "generated"), _track("zh", False, "manual")]
    mocker.patch.object(fetcher._api, "list", return_value=tracks)

    result = fetcher.get_transcript("dQw4w9WgXcQ", "zh")

    assert result.available is True
    assert result.text == "manual"
    assert result.available_languages == ["zh"]


def test_missing_requested_language_returns_promptly(mocker):
    fetcher = TranscriptFetcher()
    mocker.patch.object(fetcher._api, "list", return_value=[_track("en", False, "text")])

    result = fetcher.get_transcript("dQw4w9WgXcQ", "fr")

    assert result.available is False
    assert result.reason == "requested_language_unavailable"
    assert result.available_languages == ["en"]


def test_disabled_transcripts_return_unavailable(mocker):
    fetcher = TranscriptFetcher()
    mocker.patch.object(
        fetcher._api, "list", side_effect=TranscriptsDisabled("dQw4w9WgXcQ")
    )

    result = fetcher.get_transcript("dQw4w9WgXcQ")

    assert result.available is False
    assert result.reason == "TranscriptsDisabled"
