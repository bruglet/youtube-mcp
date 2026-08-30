from pydantic import BaseModel, Field


class Thumbnail(BaseModel):
    url: str
    width: int | None = None
    height: int | None = None


class VideoStatistics(BaseModel):
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None


class VideoMetadata(BaseModel):
    id: str
    canonical_url: str
    title: str
    description: str
    channel_id: str
    channel_title: str
    duration: str
    duration_seconds: float
    published_at: str
    statistics: VideoStatistics
    thumbnails: dict[str, Thumbnail]
    tags: list[str] = Field(default_factory=list)
    category_id: str | None = None
    caption_available: bool = False
    live_broadcast_content: str = "none"


class TranscriptSegment(BaseModel):
    start: float
    end: float
    duration: float
    text: str


class TranscriptResult(BaseModel):
    video_id: str
    available: bool
    requested_language: str | None = None
    language_code: str | None = None
    language_name: str | None = None
    is_generated: bool | None = None
    is_translatable: bool | None = None
    available_languages: list[str] = Field(default_factory=list)
    reason: str | None = None
    text: str = Field(
        default="",
        description="Included when output is text or both.",
    )
    segments: list[TranscriptSegment] = Field(
        default_factory=list,
        description="Included when output is segments or both.",
    )


class VideoSearchResult(BaseModel):
    video_id: str
    canonical_url: str
    title: str
    description: str
    channel_title: str
    published_at: str
    thumbnail_url: str


class WhisperSegment(BaseModel):
    start: float
    end: float
    text: str


class TranscriptionResult(BaseModel):
    video_id: str
    canonical_url: str
    text: str
    segments: list[WhisperSegment]
    language_code: str | None = None
    language_probability: float | None = None
    model: str
    cached: bool = False


class TranscriptionToolOutput(TranscriptionResult):
    text: str = Field(
        default="",
        description="Included when output is text or both.",
    )
    segments: list[WhisperSegment] = Field(
        default_factory=list,
        description="Included when output is segments or both.",
    )


class ArtifactMetadata(BaseModel):
    artifact_id: str
    video_id: str
    canonical_url: str
    filename: str
    mime_type: str = "video/mp4"
    size: int
    max_height: int
    download_url: str
    created_at: str
    expires_at: str
    cached: bool = False
