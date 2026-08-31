import pytest

from youtube_mcp.video import normalize_playlist, normalize_video


@pytest.mark.parametrize(
    "value",
    [
        "dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=example",
        "https://youtu.be/dQw4w9WgXcQ?t=10",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
    ],
)
def test_normalize_video(value):
    video_id, url = normalize_video(value)
    assert video_id == "dQw4w9WgXcQ"
    assert url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "value",
    ["", "short", "https://example.com/watch?v=dQw4w9WgXcQ", "youtube.com/playlist?list=x"],
)
def test_rejects_invalid_video(value):
    with pytest.raises(ValueError, match="valid YouTube"):
        normalize_video(value)


@pytest.mark.parametrize(
    "value",
    [
        "PL1234567890",
        "https://www.youtube.com/playlist?list=PL1234567890",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1234567890",
        "https://youtu.be/dQw4w9WgXcQ?list=PL1234567890",
    ],
)
def test_normalize_playlist(value):
    playlist_id, url = normalize_playlist(value)
    assert playlist_id == "PL1234567890"
    assert url == "https://www.youtube.com/playlist?list=PL1234567890"


@pytest.mark.parametrize(
    "value",
    ["", "has spaces", "https://example.com/playlist?list=PL1234567890", "youtube.com/playlist"],
)
def test_rejects_invalid_playlist(value):
    with pytest.raises(ValueError, match="valid YouTube"):
        normalize_playlist(value)
