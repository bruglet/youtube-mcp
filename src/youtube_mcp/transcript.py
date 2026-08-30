from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import CouldNotRetrieveTranscript

from .models import TranscriptResult, TranscriptSegment


class TranscriptFetcher:
    def __init__(self) -> None:
        self._api = YouTubeTranscriptApi()

    def get_transcript(self, video_id: str, language: str | None = None) -> TranscriptResult:
        try:
            tracks = list(self._api.list(video_id))
            available_languages = sorted({track.language_code for track in tracks})
            matching = tracks
            if language:
                requested = language.lower()
                matching = [
                    track
                    for track in tracks
                    if track.language_code.lower() == requested
                    or track.language_code.lower().startswith(f"{requested}-")
                ]
                matching.sort(key=lambda track: track.is_generated)
            else:
                matching.sort(
                    key=lambda track: (
                        track.is_generated,
                        not track.language_code.lower().startswith("en"),
                    )
                )

            if not matching:
                return TranscriptResult(
                    video_id=video_id,
                    available=False,
                    requested_language=language,
                    available_languages=available_languages,
                    reason="requested_language_unavailable" if language else "no_transcript",
                )

            track = matching[0]
            fetched = track.fetch()
        except CouldNotRetrieveTranscript as exc:
            return TranscriptResult(
                video_id=video_id,
                available=False,
                requested_language=language,
                reason=exc.__class__.__name__,
            )

        segments = [
            TranscriptSegment(
                start=snippet.start,
                end=snippet.start + snippet.duration,
                duration=snippet.duration,
                text=snippet.text,
            )
            for snippet in fetched
        ]
        return TranscriptResult(
            video_id=video_id,
            available=True,
            requested_language=language,
            language_code=track.language_code,
            language_name=track.language,
            is_generated=track.is_generated,
            is_translatable=track.is_translatable,
            available_languages=available_languages,
            text=" ".join(segment.text for segment in segments),
            segments=segments,
        )
