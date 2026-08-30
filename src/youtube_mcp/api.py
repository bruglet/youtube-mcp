import json
import re

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .models import (
    ChannelSearchResult,
    ChannelUploadsPage,
    Thumbnail,
    VideoMetadata,
    VideoSearchResult,
    VideoStatistics,
)
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
                "Set YOUTUBE_API_KEY to use get_video_details, search_videos, "
                "search_channels, or get_channel_uploads."
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

    def search_channels(
        self,
        query: str,
        max_results: int = 5,
    ) -> list[ChannelSearchResult]:
        service = self._require_service()
        response = _execute(
            service.search().list(
                part="snippet",
                q=query,
                type="channel",
                maxResults=min(max(1, max_results), 50),
            )
        )

        results = []
        for item in response.get("items", []):
            channel_id = item["id"].get("channelId")
            if not channel_id:
                continue
            snippet = item["snippet"]
            results.append(
                ChannelSearchResult(
                    channel_id=channel_id,
                    channel_url=f"https://www.youtube.com/channel/{channel_id}",
                    title=snippet["title"],
                    description=snippet.get("description", ""),
                    published_at=snippet["publishedAt"],
                    thumbnail_url=snippet["thumbnails"]["default"]["url"],
                )
            )
        return results

    def get_channel_uploads(
        self,
        channel_id: str,
        max_results: int = 10,
        page_token: str | None = None,
    ) -> ChannelUploadsPage:
        service = self._require_service()
        channel_response = _execute(
            service.channels().list(
                part="snippet,contentDetails",
                id=channel_id,
            )
        )
        channels = channel_response.get("items", [])
        if not channels:
            raise ValueError(f"Channel not found: {channel_id}")

        channel = channels[0]
        channel_id = channel["id"]
        channel_title = channel["snippet"]["title"]
        uploads_playlist_id = (
            channel.get("contentDetails", {})
            .get("relatedPlaylists", {})
            .get("uploads")
        )
        if not uploads_playlist_id:
            raise ValueError(f"Channel has no accessible uploads playlist: {channel_id}")

        params: dict = {
            "part": "snippet,contentDetails",
            "playlistId": uploads_playlist_id,
            "maxResults": min(max(1, max_results), 50),
        }
        if page_token:
            params["pageToken"] = page_token
        uploads_response = _execute(service.playlistItems().list(**params))

        videos = []
        for item in uploads_response.get("items", []):
            snippet = item.get("snippet", {})
            details = item.get("contentDetails", {})
            video_id = details.get("videoId") or snippet.get("resourceId", {}).get(
                "videoId"
            )
            if not video_id:
                continue
            videos.append(
                VideoSearchResult(
                    video_id=video_id,
                    canonical_url=canonical_url(video_id),
                    title=snippet.get("title", ""),
                    description=snippet.get("description", ""),
                    channel_title=channel_title,
                    published_at=details.get("videoPublishedAt")
                    or snippet.get("publishedAt", ""),
                    thumbnail_url=snippet.get("thumbnails", {})
                    .get("default", {})
                    .get("url", ""),
                )
            )

        return ChannelUploadsPage(
            channel_id=channel_id,
            channel_url=f"https://www.youtube.com/channel/{channel_id}",
            channel_title=channel_title,
            videos=videos,
            next_page_token=uploads_response.get("nextPageToken"),
        )
