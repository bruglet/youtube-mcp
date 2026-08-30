import asyncio
import json
from pathlib import Path
import shutil
import sys
from typing import Any


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
    ) -> Path:
        await self._run(
            "--no-playlist",
            "--no-overwrites",
            "--format",
            f"bv*[height<={max_height}]+ba/b[height<={max_height}]",
            "--merge-output-format",
            "mp4",
            "--remux-video",
            "mp4",
            "--max-filesize",
            str(max_bytes),
            "--output",
            str(output_template),
            url,
        )
        path = self._find_output(output_template)
        if path.suffix.lower() != ".mp4":
            raise YtDlpError("yt-dlp could not make an MP4 artifact for this video.")
        if path.stat().st_size > max_bytes:
            path.unlink(missing_ok=True)
            raise YtDlpError("The downloaded video is larger than the artifact size limit.")
        return path

    async def _run(self, *arguments: str) -> str:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "yt_dlp",
            "--no-color",
            "--quiet",
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(self._timeout_seconds):
                stdout, stderr = await process.communicate()
        except TimeoutError as exc:
            await self._stop(process)
            raise YtDlpError("yt-dlp exceeded the operation timeout.") from exc
        except asyncio.CancelledError:
            await self._stop(process)
            raise

        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="replace").strip()
            if len(message) > 2000:
                message = message[-2000:]
            raise YtDlpError(f"yt-dlp failed: {message or 'unknown error'}")
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
    def _find_output(template: Path) -> Path:
        prefix = template.name.split("%", 1)[0]
        matches = [
            path
            for path in template.parent.glob(f"{prefix}*")
            if path.is_file() and not path.name.endswith((".part", ".ytdl"))
        ]
        if not matches:
            raise YtDlpError("yt-dlp completed without creating a media file.")
        return max(matches, key=lambda path: path.stat().st_size)


def remove_directory(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
