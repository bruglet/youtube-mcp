from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import re
import tempfile

from .downloader import YtDlpRunner, remove_directory
from .models import ArtifactMetadata


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MediaCache:
    def __init__(self, root: Path, ttl_seconds: int, max_bytes: int) -> None:
        self.root = root
        self.audio_dir = root / "audio"
        self.artifact_dir = root / "artifacts"
        self.work_dir = root / "work"
        self.ttl_seconds = ttl_seconds
        self.max_bytes = max_bytes
        for path in (self.audio_dir, self.artifact_dir, self.work_dir):
            path.mkdir(parents=True, exist_ok=True)

    def cleanup(self) -> None:
        cutoff = _utc_now().timestamp() - self.ttl_seconds
        for path in self.work_dir.iterdir():
            if path.stat().st_mtime < cutoff:
                remove_directory(path) if path.is_dir() else path.unlink(missing_ok=True)

        media_files = [
            path
            for path in self.root.rglob("*")
            if path.is_file() and path.suffix != ".json"
        ]
        for path in media_files:
            if path.stat().st_mtime < cutoff:
                self._remove_media(path)

        media_files = [path for path in media_files if path.exists()]
        total = sum(path.stat().st_size for path in media_files)
        for path in sorted(media_files, key=lambda item: item.stat().st_mtime):
            if total <= self.max_bytes:
                break
            size = path.stat().st_size
            self._remove_media(path)
            total -= size

    def cached_audio(self, video_id: str) -> Path | None:
        key = self.key(f"audio:{video_id}")
        matches = list(self.audio_dir.glob(f"{key}.*"))
        if not matches:
            return None
        path = matches[0]
        if path.stat().st_mtime < _utc_now().timestamp() - self.ttl_seconds:
            path.unlink(missing_ok=True)
            return None
        os.utime(path)
        return path

    def store_audio(self, video_id: str, source: Path) -> Path:
        key = self.key(f"audio:{video_id}")
        destination = self.audio_dir / f"{key}{source.suffix.lower()}"
        os.replace(source, destination)
        return destination

    def make_work_dir(self) -> Path:
        return Path(tempfile.mkdtemp(prefix="youtube-mcp-", dir=self.work_dir))

    @staticmethod
    def key(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _remove_media(path: Path) -> None:
        path.unlink(missing_ok=True)
        path.with_suffix(".json").unlink(missing_ok=True)


class ArtifactStore:
    def __init__(
        self,
        cache: MediaCache,
        downloader: YtDlpRunner,
        public_base_url: str,
        ttl_seconds: int,
        max_artifact_bytes: int,
        max_duration_seconds: int,
    ) -> None:
        self._cache = cache
        self._downloader = downloader
        self._public_base_url = public_base_url.rstrip("/")
        self._ttl_seconds = ttl_seconds
        self._max_artifact_bytes = max_artifact_bytes
        self._max_duration_seconds = max_duration_seconds

    async def materialize(
        self,
        video_id: str,
        canonical_url: str,
        max_height: int,
    ) -> ArtifactMetadata:
        artifact_id = self._cache.key(f"video:{video_id}:{max_height}")
        cached = self.resolve(artifact_id)
        if cached:
            return cached[0]

        info = await self._downloader.probe(canonical_url)
        duration = float(info.get("duration") or 0)
        if not duration:
            raise ValueError("The video duration is not available.")
        if duration > self._max_duration_seconds:
            raise ValueError("The video is longer than the configured duration limit.")
        if info.get("is_live"):
            raise ValueError("Live video materialization is not supported.")

        work_dir = self._cache.make_work_dir()
        try:
            source = await self._downloader.download_video(
                canonical_url,
                work_dir / "video.%(ext)s",
                max_height,
                self._max_artifact_bytes,
            )
            if source.stat().st_size > self._cache.max_bytes:
                raise ValueError("The downloaded video is larger than the total cache limit.")
            destination = self._cache.artifact_dir / f"{artifact_id}.mp4"
            os.replace(source, destination)
            now = _utc_now()
            title = _safe_filename(str(info.get("title") or video_id))
            metadata = ArtifactMetadata(
                artifact_id=artifact_id,
                video_id=video_id,
                canonical_url=canonical_url,
                filename=f"{title}.mp4",
                size=destination.stat().st_size,
                max_height=max_height,
                download_url=f"{self._public_base_url}/artifacts/{artifact_id}",
                created_at=now.isoformat(),
                expires_at=(now + timedelta(seconds=self._ttl_seconds)).isoformat(),
            )
            self._metadata_path(artifact_id).write_text(
                metadata.model_dump_json(indent=2), encoding="utf-8"
            )
            self._cache.cleanup()
            return metadata
        finally:
            remove_directory(work_dir)

    def resolve(self, artifact_id: str) -> tuple[ArtifactMetadata, Path] | None:
        if not re.fullmatch(r"[a-f0-9]{64}", artifact_id):
            return None
        media_path = self._cache.artifact_dir / f"{artifact_id}.mp4"
        metadata_path = self._metadata_path(artifact_id)
        if not media_path.is_file() or not metadata_path.is_file():
            return None
        try:
            metadata = ArtifactMetadata.model_validate_json(metadata_path.read_text("utf-8"))
            expires_at = datetime.fromisoformat(metadata.expires_at)
        except (ValueError, OSError):
            media_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            return None
        if expires_at <= _utc_now():
            media_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            return None
        os.utime(media_path)
        return metadata, media_path

    def _metadata_path(self, artifact_id: str) -> Path:
        return self._cache.artifact_dir / f"{artifact_id}.json"


def _safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return (value or "youtube-video")[:120]
