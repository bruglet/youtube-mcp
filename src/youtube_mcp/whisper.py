import asyncio
import gc
from pathlib import Path
import threading
from typing import Any, Literal

from .cache import MediaCache
from .downloader import YtDlpRunner, remove_directory
from .models import TranscriptionResult, WhisperSegment


ModelChoice = Literal["auto", "english", "multilingual"]


class WhisperModelManager:
    def __init__(
        self,
        english_model: str,
        multilingual_model: str,
        compute_type: str,
        cpu_threads: int,
        idle_seconds: int,
        beam_size: int,
        model_cache_dir: Path,
    ) -> None:
        self._models = {"english": english_model, "multilingual": multilingual_model}
        self._compute_type = compute_type
        self._cpu_threads = cpu_threads
        self._idle_seconds = idle_seconds
        self._beam_size = beam_size
        self._model_cache_dir = model_cache_dir
        self._model: Any = None
        self._loaded_name: str | None = None
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def transcribe(
        self,
        audio_path: Path,
        language: str | None,
        model_choice: ModelChoice,
        video_id: str,
        canonical_url: str,
        cancel_event: threading.Event | None = None,
    ) -> TranscriptionResult:
        family = self._select_family(language, model_choice)
        checkpoint = self._models[family]
        with self._lock:
            self._cancel_timer()
            model = self._load(checkpoint)
            arguments: dict[str, Any] = {
                "beam_size": self._beam_size,
                "vad_filter": True,
            }
            if language:
                arguments["language"] = language.split("-", 1)[0].lower()
            try:
                segments_iter, info = model.transcribe(str(audio_path), **arguments)
                segments = []
                for item in segments_iter:
                    if cancel_event and cancel_event.is_set():
                        raise ValueError("Local transcription was canceled.")
                    segments.append(
                        WhisperSegment(
                            start=item.start,
                            end=item.end,
                            text=item.text.strip(),
                        )
                    )
            except Exception as exc:
                raise ValueError(f"Local transcription failed: {exc}") from exc
            finally:
                self._schedule_unload()

        return TranscriptionResult(
            video_id=video_id,
            canonical_url=canonical_url,
            text=" ".join(segment.text for segment in segments),
            segments=segments,
            language_code=getattr(info, "language", None),
            language_probability=getattr(info, "language_probability", None),
            model=checkpoint,
        )

    def unload(self) -> None:
        with self._lock:
            self._cancel_timer()
            self._unload_locked()

    def _load(self, checkpoint: str) -> Any:
        if self._model is not None and self._loaded_name == checkpoint:
            return self._model
        self._unload_locked()
        from faster_whisper import WhisperModel

        self._model_cache_dir.mkdir(parents=True, exist_ok=True)
        self._model = WhisperModel(
            checkpoint,
            device="cpu",
            compute_type=self._compute_type,
            cpu_threads=self._cpu_threads,
            num_workers=1,
            download_root=str(self._model_cache_dir),
        )
        self._loaded_name = checkpoint
        return self._model

    def result_cache_key(
        self, video_id: str, language: str | None, choice: ModelChoice
    ) -> str:
        family = self._select_family(language, choice)
        language_key = (language or "auto").split("-", 1)[0].lower()
        checkpoint = self._models[family]
        return (
            f"transcription:v1:{video_id}:{checkpoint}:"
            f"{language_key}:beam={self._beam_size}"
        )

    @staticmethod
    def _select_family(language: str | None, choice: ModelChoice) -> str:
        if choice != "auto":
            return choice
        if not language or language.lower().startswith("en"):
            return "english"
        return "multilingual"

    def _schedule_unload(self) -> None:
        self._timer = threading.Timer(self._idle_seconds, self.unload)
        self._timer.daemon = True
        self._timer.start()

    def _cancel_timer(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _unload_locked(self) -> None:
        self._model = None
        self._loaded_name = None
        gc.collect()


class WhisperTranscriber:
    def __init__(
        self,
        manager: WhisperModelManager,
        downloader: YtDlpRunner,
        cache: MediaCache,
        max_duration_seconds: int,
    ) -> None:
        self._manager = manager
        self._downloader = downloader
        self._cache = cache
        self._max_duration_seconds = max_duration_seconds
        self._semaphore = asyncio.Semaphore(1)

    async def transcribe(
        self,
        video_id: str,
        canonical_url: str,
        language: str | None,
        model_choice: ModelChoice,
    ) -> TranscriptionResult:
        async with self._semaphore:
            cache_key = self._manager.result_cache_key(
                video_id, language, model_choice
            )
            cached = self._cache.cached_transcription(cache_key)
            if cached is not None:
                return cached

            info = await self._downloader.probe(canonical_url)
            duration = float(info.get("duration") or 0)
            if not duration:
                raise ValueError("The video duration is not available.")
            if duration > self._max_duration_seconds:
                raise ValueError("The video is longer than the configured duration limit.")
            if info.get("is_live"):
                raise ValueError("Live video transcription is not supported.")

            audio_path = self._cache.cached_audio(video_id)
            if audio_path is None:
                work_dir = self._cache.make_work_dir()
                try:
                    source = await self._downloader.download_audio(
                        canonical_url, work_dir / "audio.%(ext)s"
                    )
                    audio_path = self._cache.store_audio(video_id, source)
                finally:
                    remove_directory(work_dir)

            cancel_event = threading.Event()
            try:
                result = await asyncio.to_thread(
                    self._manager.transcribe,
                    audio_path,
                    language,
                    model_choice,
                    video_id,
                    canonical_url,
                    cancel_event,
                )
            except asyncio.CancelledError:
                cancel_event.set()
                raise
            self._cache.store_transcription(cache_key, result)
            self._cache.cleanup()
            return result
