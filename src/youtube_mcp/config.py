from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero.")
    return value


@dataclass(frozen=True)
class Settings:
    youtube_api_key: str
    public_base_url: str
    cf_access_team_domain: str
    cf_access_audiences: tuple[str, ...]
    auth_mode: str = "cloudflare"
    host: str = "127.0.0.1"
    port: int = 8000
    cache_dir: Path = Path("/data/cache")
    model_cache_dir: Path = Path("/data/models")
    max_video_duration_seconds: int = 3600
    long_operation_timeout_seconds: int = 7200
    cache_ttl_seconds: int = 86400
    cache_max_bytes: int = 50 * 1024**3
    artifact_max_bytes: int = 8 * 1024**3
    whisper_en_model: str = "small.en"
    whisper_multilingual_model: str = "small"
    whisper_compute_type: str = "int8"
    whisper_cpu_threads: int = 4
    whisper_model_idle_seconds: int = 300
    whisper_beam_size: int = 5

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        auth_mode = os.getenv("AUTH_MODE", "cloudflare").lower()
        if auth_mode not in {"cloudflare", "disabled"}:
            raise RuntimeError("AUTH_MODE must be cloudflare or disabled.")

        api_key = os.getenv("YOUTUBE_API_KEY", "").strip()

        public_base_url = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        parsed = urlparse(public_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("PUBLIC_BASE_URL must be an HTTP or HTTPS URL.")

        team_domain = os.getenv("CF_ACCESS_TEAM_DOMAIN", "").strip()
        audiences = tuple(
            value.strip()
            for value in os.getenv("CF_ACCESS_AUDIENCE", "").split(",")
            if value.strip()
        )
        if auth_mode == "cloudflare" and (not team_domain or not audiences):
            raise RuntimeError(
                "CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUDIENCE are required in cloudflare mode."
            )

        host = os.getenv("HOST", "127.0.0.1")
        if auth_mode == "disabled" and host not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError("AUTH_MODE=disabled requires a loopback HOST.")

        return cls(
            youtube_api_key=api_key,
            public_base_url=public_base_url,
            cf_access_team_domain=team_domain,
            cf_access_audiences=audiences,
            auth_mode=auth_mode,
            host=host,
            port=_positive_int("PORT", 8000),
            cache_dir=Path(os.getenv("CACHE_DIR", "/data/cache")),
            model_cache_dir=Path(os.getenv("MODEL_CACHE_DIR", "/data/models")),
            max_video_duration_seconds=_positive_int("MAX_VIDEO_DURATION_SECONDS", 3600),
            long_operation_timeout_seconds=_positive_int("LONG_OPERATION_TIMEOUT_SECONDS", 7200),
            cache_ttl_seconds=_positive_int("CACHE_TTL_SECONDS", 86400),
            cache_max_bytes=_positive_int("CACHE_MAX_BYTES", 50 * 1024**3),
            artifact_max_bytes=_positive_int("ARTIFACT_MAX_BYTES", 8 * 1024**3),
            whisper_en_model=os.getenv("WHISPER_EN_MODEL", "small.en"),
            whisper_multilingual_model=os.getenv("WHISPER_MULTILINGUAL_MODEL", "small"),
            whisper_compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "int8"),
            whisper_cpu_threads=_positive_int("WHISPER_CPU_THREADS", 4),
            whisper_model_idle_seconds=_positive_int("WHISPER_MODEL_IDLE_SECONDS", 300),
            whisper_beam_size=_positive_int("WHISPER_BEAM_SIZE", 5),
        )

    @property
    def cf_issuer(self) -> str:
        domain = self.cf_access_team_domain.rstrip("/")
        if domain.startswith("https://"):
            return domain
        return f"https://{domain}"
