import asyncio
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .captions import normalize_srv3_windows
from .models import CaptionOption, VideoFormatOption, VideoFormatsResult


class YtDlpError(ValueError):
    pass


class YtDlpRunner:
    def __init__(self, timeout_seconds: int) -> None:
        self._timeout_seconds = timeout_seconds

    async def probe(self, url: str) -> dict[str, Any]:
        stdout = await self._run(
            "--dump-single-json", "--skip-download", "--no-playlist", url
        )
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise YtDlpError("yt-dlp returned invalid video metadata.") from exc

    async def download_audio(self, url: str, output_template: Path) -> Path:
        await self._run(
            "--no-playlist",
            "--no-overwrites",
            "--format",
            "bestaudio/best",
            "--output",
            str(output_template),
            url,
        )
        return self._find_output(output_template)

    async def download_video(
        self,
        url: str,
        output_template: Path,
        max_height: int,
        max_bytes: int,
        max_fps: int | None = None,
        dynamic_range: str = "auto",
        caption_tracks: list[CaptionOption] | None = None,
    ) -> Path:
        advanced = max_height > 720 or max_fps is not None or dynamic_range != "auto"
        video_filter = f"[height<={max_height}]"
        if max_fps is not None:
            video_filter += f"[fps<={max_fps}]"
        if dynamic_range == "hdr":
            video_filter += "[dynamic_range!=SDR]"
        elif dynamic_range == "sdr":
            video_filter += "[dynamic_range=SDR]"
        container = "mkv" if advanced else "mp4"
        subtitle_args = []
        if caption_tracks:
            subtitle_args = [
                "--extractor-args", "youtube:skip=translated_subs",
                "--sub-langs", ",".join(f"^{re.escape(track.language_code)}$" for track in caption_tracks),
                "--sub-format", "srv3/vtt" if advanced else "vtt",
            ]
            if not advanced:
                subtitle_args.append("--embed-subs")
            if len(caption_tracks) > 2:
                subtitle_args.extend(("--sleep-subtitles", "5"))
            if any(track.source == "manual" for track in caption_tracks):
                subtitle_args.append("--write-subs")
            if any(track.source == "automatic" for track in caption_tracks):
                subtitle_args.append("--write-auto-subs")
        await self._run(
            "--no-playlist",
            "--no-overwrites",
            "--format",
            f"bv*{video_filter}+ba/b{video_filter}",
            "--format-sort",
            "res,fps,hdr:12,vcodec,acodec",
            "--merge-output-format",
            container,
            "--remux-video",
            container,
            "--max-filesize",
            str(max_bytes),
            "--output",
            str(output_template),
            *subtitle_args,
            url,
        )
        path = self._find_output(output_template, f".{container}")
        if advanced and caption_tracks:
            await self._embed_styled_subtitles(path, caption_tracks)
        if path.stat().st_size > max_bytes:
            path.unlink(missing_ok=True)
            raise YtDlpError("The downloaded video is larger than the artifact size limit.")
        return path

    async def _embed_styled_subtitles(
        self, video_path: Path, caption_tracks: list[CaptionOption]
    ) -> None:
        inputs: list[Path] = []
        for track in caption_tracks:
            stem = f"{video_path.stem}.{track.language_code}"
            srv3 = video_path.with_name(f"{stem}.srv3")
            vtt = video_path.with_name(f"{stem}.vtt")
            ass = video_path.with_name(f"{stem}.ass")
            if srv3.is_file():
                normalize_srv3_windows(srv3)
                await self._run_process(
                    "YTSubConverter",
                    "dotnet", "/opt/caption-converter/HeadlessCaptionConverter.dll",
                    str(srv3),
                    str(ass),
                )
            elif vtt.is_file():
                await self._run_process(
                    "ffmpeg", "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-i", str(vtt), str(ass),
                )
            else:
                raise YtDlpError(
                    f"yt-dlp did not download the {track.language_code} caption track."
                )
            if not ass.is_file():
                raise YtDlpError(
                    f"Could not convert the {track.language_code} caption track to ASS."
                )
            inputs.append(ass)

        output = video_path.with_name(f"{video_path.stem}.styled.mkv")
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video_path)]
        for subtitle in inputs:
            args.extend(("-i", str(subtitle)))
        args.extend(("-map", "0:v", "-map", "0:a?"))
        for index, track in enumerate(caption_tracks):
            args.extend(("-map", f"{index + 1}:0"))
            args.extend((f"-metadata:s:s:{index}", f"language={track.language_code.split('-')[0]}"))
            args.extend((f"-metadata:s:s:{index}", f"title={track.language_code}"))
        args.extend(("-c", "copy", str(output)))
        await self._run_process("ffmpeg", *args)
        output.replace(video_path)

    async def _run(self, *arguments: str) -> str:
        return await self._run_process("yt-dlp", sys.executable, "-m", "yt_dlp", "--no-color", "--quiet", *arguments)

    async def _run_process(self, label: str, *arguments: str) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise YtDlpError(f"Could not start {label}: {exc}") from exc
        try:
            async with asyncio.timeout(self._timeout_seconds):
                stdout, stderr = await process.communicate()
        except TimeoutError as exc:
            await self._stop(process)
            raise YtDlpError(f"{label} exceeded the operation timeout.") from exc
        except asyncio.CancelledError:
            await self._stop(process)
            raise

        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="replace").strip()
            if len(message) > 2000:
                message = message[-2000:]
            raise YtDlpError(f"{label} failed: {message or 'unknown error'}")
        return stdout.decode("utf-8", errors="replace")

    @staticmethod
    async def _stop(process: asyncio.subprocess.Process) -> None:
        process.terminate()
        try:
            async with asyncio.timeout(5):
                await process.wait()
        except TimeoutError:
            process.kill()
            await process.wait()

    @staticmethod
    def _find_output(template: Path, suffix: str | None = None) -> Path:
        prefix = template.name.split("%", 1)[0]
        matches = [
            path
            for path in template.parent.glob(f"{prefix}*")
            if path.is_file()
            and not path.name.endswith((".part", ".ytdl"))
            and (suffix is None or path.suffix.lower() == suffix)
        ]
        if not matches:
            raise YtDlpError("yt-dlp completed without creating a media file.")
        return max(matches, key=lambda path: path.stat().st_size)


def video_formats(video_id: str, canonical_url: str, info: dict[str, Any]) -> VideoFormatsResult:
    groups: dict[tuple[int, float | None, str], set[str]] = {}
    audio_codecs: set[str] = set()
    for item in info.get("formats") or []:
        vcodec = str(item.get("vcodec") or "none")
        acodec = str(item.get("acodec") or "none")
        if vcodec == "none":
            if acodec != "none":
                audio_codecs.add(_codec_name(acodec))
            continue
        height = item.get("height")
        if not isinstance(height, (int, float)) or height <= 0:
            continue
        fps = item.get("fps")
        fps = round(float(fps), 2) if isinstance(fps, (int, float)) else None
        key = (int(height), fps, str(item.get("dynamic_range") or "unknown"))
        groups.setdefault(key, set()).add(_codec_name(vcodec))
    ordered = sorted(groups, key=lambda key: (key[0], key[1] or 0, key[2]), reverse=True)
    return VideoFormatsResult(
        video_id=video_id,
        canonical_url=canonical_url,
        options=[
            VideoFormatOption(
                height=height,
                fps=fps,
                dynamic_range=dynamic_range,
                video_codecs=sorted(groups[(height, fps, dynamic_range)], key=_codec_order),
            )
            for height, fps, dynamic_range in ordered[:25]
        ],
        audio_codecs=sorted(audio_codecs, key=_codec_order),
        caption_tracks=source_caption_tracks(info),
        truncated=len(ordered) > 25,
    )


def source_caption_tracks(
    info: dict[str, Any], requested_language: str | None = None
) -> list[CaptionOption]:
    tracks = {
        code: CaptionOption(language_code=code, source="manual")
        for code, formats in (info.get("subtitles") or {}).items()
        if formats and code != "live_chat"
    }
    for code, formats in (info.get("automatic_captions") or {}).items():
        if code in tracks or code.endswith("-orig") or not formats:
            continue
        entry = next((item for item in formats if item.get("url")), None)
        if entry is None or "tlang" in parse_qs(urlsplit(entry["url"]).query):
            continue
        if " from " in str(entry.get("name") or ""):
            continue
        tracks[code] = CaptionOption(language_code=code, source="automatic")

    if requested_language is None:
        return sorted(tracks.values(), key=lambda track: track.language_code)

    requested = requested_language.strip().lower()
    candidates = [
        track for track in tracks.values()
        if track.language_code.lower() == requested
        or track.language_code.lower().startswith(f"{requested}-")
        or requested.startswith(f"{track.language_code.lower()}-")
    ]
    if not candidates:
        return []
    candidates.sort(key=lambda track: (
        track.source != "manual" if requested == "en" else track.language_code.lower() != requested,
        track.language_code.lower() != requested if requested == "en" else track.source != "manual",
        track.language_code.lower() != "en-us" if requested == "en" else False,
        track.language_code,
    ))
    return candidates[:1]


def _codec_name(value: str) -> str:
    if value.startswith("av01"):
        return "AV1"
    if value.startswith("vp9"):
        return "VP9"
    if value.startswith(("hev1", "hvc1", "h265")):
        return "HEVC"
    if value.startswith(("avc1", "h264")):
        return "H.264"
    if value.startswith("mp4a"):
        return "AAC"
    if value.startswith("opus"):
        return "Opus"
    return value


def _codec_order(value: str) -> tuple[int, str]:
    order = {"AV1": 0, "VP9": 1, "HEVC": 2, "H.264": 3, "Opus": 0, "AAC": 1}
    return order.get(value, 10), value


def remove_directory(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
