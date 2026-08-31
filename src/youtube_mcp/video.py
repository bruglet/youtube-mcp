import re
from urllib.parse import parse_qs, urlparse


_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{2,200}$")


def normalize_video(value: str) -> tuple[str, str]:
    """Return a video ID and its canonical watch URL."""
    value = value.strip()
    if _VIDEO_ID.fullmatch(value):
        return value, canonical_url(value)

    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.hostname or "").lower()
    video_id: str | None = None

    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/", 1)[0]
    elif (
        host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtube-nocookie.com"
        or host.endswith(".youtube-nocookie.com")
    ):
        parts = [part for part in parsed.path.split("/") if part]
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [None])[0]
        elif len(parts) >= 2 and parts[0] in {"shorts", "live", "embed"}:
            video_id = parts[1]

    if not video_id or not _VIDEO_ID.fullmatch(video_id):
        raise ValueError("Enter a valid YouTube video ID or video URL.")
    return video_id, canonical_url(video_id)


def canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def normalize_playlist(value: str) -> tuple[str, str]:
    """Return a playlist ID and its canonical URL."""
    value = value.strip()
    if _PLAYLIST_ID.fullmatch(value):
        return value, playlist_url(value)

    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.hostname or "").lower()
    if not (
        host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtu.be"
    ):
        raise ValueError("Enter a valid YouTube playlist ID or playlist URL.")

    playlist_id = parse_qs(parsed.query).get("list", [None])[0]
    if not playlist_id or not _PLAYLIST_ID.fullmatch(playlist_id):
        raise ValueError("Enter a valid YouTube playlist ID or playlist URL.")
    return playlist_id, playlist_url(playlist_id)


def playlist_url(playlist_id: str) -> str:
    return f"https://www.youtube.com/playlist?list={playlist_id}"
