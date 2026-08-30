import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from youtube_mcp.api import YouTubeAPI, duration_seconds
from youtube_mcp.models import VideoMetadata, VideoSearchResult


_MOCK_RESPONSE = {
    "items": [
        {
            "id": "dQw4w9WgXcQ",
            "snippet": {
                "title": "Rick Astley - Never Gonna Give You Up",
                "description": "The official video.",
                "channelId": "UC38IQsAvIsxxjztdMZQtwHA",
                "channelTitle": "Rick Astley",
                "publishedAt": "2009-10-25T06:57:33Z",
                "categoryId": "10",
                "tags": ["music"],
                "liveBroadcastContent": "none",
                "thumbnails": {
                    "default": {
                        "url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/default.jpg",
                        "width": 120,
                        "height": 90,
                    }
                },
            },
            "statistics": {
                "viewCount": "1000000000",
                "likeCount": "15000000",
                "commentCount": "2000000",
            },
            "contentDetails": {"duration": "PT3M33S", "caption": "true"},
        }
    ]
}


def _make_api(mocker, response):
    mock_build = mocker.patch("youtube_mcp.api.build")
    mock_service = mock_build.return_value
    mock_service.videos.return_value.list.return_value.execute.return_value = response
    return YouTubeAPI("fake-key")


def test_get_video_returns_rich_metadata(mocker):
    result = _make_api(mocker, _MOCK_RESPONSE).get_video("dQw4w9WgXcQ")

    assert isinstance(result, VideoMetadata)
    assert result.id == "dQw4w9WgXcQ"
    assert result.canonical_url.endswith("v=dQw4w9WgXcQ")
    assert result.channel_id == "UC38IQsAvIsxxjztdMZQtwHA"
    assert result.duration_seconds == 213
    assert result.statistics.view_count == 1_000_000_000
    assert result.statistics.like_count == 15_000_000
    assert result.thumbnails["default"].width == 120
    assert result.caption_available is True


def test_get_video_not_found_raises(mocker):
    with pytest.raises(ValueError, match="Video not found: badid"):
        _make_api(mocker, {"items": []}).get_video("badid")


def test_api_error_has_reason_and_status(mocker):
    error = HttpError(
        Response({"status": "403"}),
        b'{"error":{"errors":[{"reason":"quotaExceeded"}]}}',
    )
    mock_build = mocker.patch("youtube_mcp.api.build")
    service = mock_build.return_value
    service.videos.return_value.list.return_value.execute.side_effect = error

    with pytest.raises(ValueError, match="quotaExceeded, HTTP 403"):
        YouTubeAPI("fake-key").get_video("dQw4w9WgXcQ")


def test_hidden_statistics_are_none(mocker):
    response = {
        "items": [
            {
                **_MOCK_RESPONSE["items"][0],
                "statistics": {"viewCount": "500"},
            }
        ]
    }
    result = _make_api(mocker, response).get_video("dQw4w9WgXcQ")
    assert result.statistics.view_count == 500
    assert result.statistics.like_count is None
    assert result.statistics.comment_count is None


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("PT3M33S", 213), ("PT1H2M3.5S", 3723.5), ("P1DT1S", 86401)],
)
def test_duration_seconds(value, seconds):
    assert duration_seconds(value) == seconds


_MOCK_SEARCH_RESPONSE = {
    "items": [
        {
            "id": {"videoId": "dQw4w9WgXcQ"},
            "snippet": {
                "title": "A video",
                "description": "A description",
                "channelTitle": "A channel",
                "publishedAt": "2026-06-20T10:00:00Z",
                "thumbnails": {"default": {"url": "https://example.com/a.jpg"}},
            },
        }
    ]
}


def test_search_videos_returns_results_and_passes_parameters(mocker):
    mock_build = mocker.patch("youtube_mcp.api.build")
    service = mock_build.return_value
    service.search.return_value.list.return_value.execute.return_value = _MOCK_SEARCH_RESPONSE
    api = YouTubeAPI("fake-key")

    results = api.search_videos("test query", max_results=5, language="en", order="date")

    assert len(results) == 1
    assert isinstance(results[0], VideoSearchResult)
    assert results[0].canonical_url.endswith("v=dQw4w9WgXcQ")
    service.search.return_value.list.assert_called_once_with(
        part="snippet",
        q="test query",
        type="video",
        maxResults=5,
        order="date",
        relevanceLanguage="en",
    )


@pytest.mark.parametrize("method", ["get_video", "search_videos"])
def test_data_api_methods_require_api_key(method):
    with pytest.raises(ValueError, match="Set YOUTUBE_API_KEY"):
        getattr(YouTubeAPI(""), method)("test")


def test_search_skips_non_video_items(mocker):
    response = {"items": [{"id": {"playlistId": "PL123"}, "snippet": {}}]}
    mock_build = mocker.patch("youtube_mcp.api.build")
    service = mock_build.return_value
    service.search.return_value.list.return_value.execute.return_value = response
    assert YouTubeAPI("fake-key").search_videos("test") == []
