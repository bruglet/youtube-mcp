import json
import re

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .models import Thumbnail, VideoMetadata, VideoSearchResult, VideoStatistics
from .video import canonical_url


_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)


def duration_seconds(value: str) -> float:
    match = _DURATION.fullmatch(value)
    if not match:
        raise ValueError(f"YouTube returned an invalid duration: {value}")
    parts = {key: float(number or 0) for key, number in match.groupdict().items()}
    return parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]


def _optional_int(values: dict, key: str) -> int | None:
    return int(values[key]) if key in values else None


def _execute(request):
    try:
        return request.execute()
    except HttpError as exc:
        reason = "unknown error"
        try:
            payload = json.loads(exc.content.decode("utf-8"))
            reason = payload["error"]["errors"][0]["reason"]
        except (KeyError, IndexError, TypeError, ValueError, UnicodeError):
            pass
        status = getattr(exc.resp, "status", "unknown")
        raise ValueError(
            f"YouTube Data API request failed ({reason}, HTTP {status})."
        ) from exc


class YouTubeAPI:
    def __init__(self, api_key: str) -> None:
        self._service = (
            build("youtube", "v3", developerKey=api_key) if api_key else None
        )

    def _require_service(self):
        if self._service is None:
            raise ValueError(
                "Set YOUTUBE_API_KEY to use get_video_details or search_videos."
            )
        return self._service

    def get_video(self, video_id: str) -> VideoMetadata:
        service = self._require_service()
        response = _execute(
            service.videos().list(
                part="snippet,statistics,contentDetails",
                id=video_id,
            )
        )

        items = response.get("items", [])
        if not items:
            raise ValueError(f"Video not found: {video_id}")

        item = items[0]
        snippet = item["snippet"]
        stats = item.get("statistics", {})
        details = item["contentDetails"]

        return VideoMetadata(
            id=item["id"],
            canonical_url=canonical_url(item["id"]),
            title=snippet["title"],
            description=snippet.get("description", ""),
            channel_id=snippet["channelId"],
            channel_title=snippet["channelTitle"],
            duration=details["duration"],
            duration_seconds=duration_seconds(details["duration"]),
            published_at=snippet["publishedAt"],
            statistics=VideoStatistics(
                view_count=_optional_int(stats, "viewCount"),
                like_count=_optional_int(stats, "likeCount"),
                comment_count=_optional_int(stats, "commentCount"),
            ),
            thumbnails={
                name: Thumbnail(**thumbnail)
                for name, thumbnail in snippet.get("thumbnails", {}).items()
            },
            tags=snippet.get("tags", []),
            category_id=snippet.get("categoryId"),
            caption_available=details.get("caption") == "true",
            live_broadcast_content=snippet.get("liveBroadcastContent", "none"),
        )

    def search_videos(
        self,
        query: str,
        max_results: int = 10,
        language: str | None = None,
        order: str = "relevance",
    ) -> list[VideoSearchResult]:
        service = self._require_service()
        params: dict = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": min(max(1, max_results), 50),
            "order": order,
        }
        if language:
            params["relevanceLanguage"] = language

        response = _execute(service.search().list(**params))

        results = []
        for item in response.get("items", []):
            video_id = item["id"].get("videoId")
            if not video_id:
                continue
            snippet = item["snippet"]
            results.append(VideoSearchResult(
                video_id=video_id,
                canonical_url=canonical_url(video_id),
                title=snippet["title"],
                description=snippet.get("description", ""),
                channel_title=snippet["channelTitle"],
                published_at=snippet["publishedAt"],
                thumbnail_url=snippet["thumbnails"]["default"]["url"],
            ))
        return results
