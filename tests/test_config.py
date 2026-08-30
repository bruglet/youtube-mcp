import pytest

from youtube_mcp.config import Settings


def test_cloudflare_mode_requires_access_settings(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "key")
    monkeypatch.setenv("AUTH_MODE", "cloudflare")
    monkeypatch.delenv("CF_ACCESS_TEAM_DOMAIN", raising=False)
    monkeypatch.delenv("CF_ACCESS_AUDIENCE", raising=False)
    with pytest.raises(RuntimeError, match="CF_ACCESS_TEAM_DOMAIN"):
        Settings.from_env()


def test_disabled_mode_supports_local_development(monkeypatch, tmp_path):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.setenv("AUTH_MODE", "disabled")
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MODEL_CACHE_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("CF_ACCESS_AUDIENCE", "first,second")

    settings = Settings.from_env()

    assert settings.auth_mode == "disabled"
    assert settings.youtube_api_key == ""
    assert settings.cache_ttl_seconds == 86400
    assert settings.cache_max_bytes == 50 * 1024**3
    assert settings.artifact_max_bytes == 8 * 1024**3
    assert settings.whisper_cpu_threads == 4


def test_issuer_accepts_domain_or_url():
    base = dict(
        youtube_api_key="key",
        public_base_url="https://mcp.example.com",
        cf_access_audiences=("aud",),
    )
    assert Settings(cf_access_team_domain="team.cloudflareaccess.com", **base).cf_issuer == (
        "https://team.cloudflareaccess.com"
    )
    settings = Settings(
        cf_access_team_domain="https://team.cloudflareaccess.com/", **base
    )
    assert settings.cf_issuer == "https://team.cloudflareaccess.com"
