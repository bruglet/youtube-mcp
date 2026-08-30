import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from youtube_mcp.config import Settings
from youtube_mcp.models import (
    ArtifactMetadata,
    TranscriptResult,
    TranscriptSegment,
)
from youtube_mcp.server import _select_transcript_output, create_app


def _settings(tmp_path: Path, auth_mode: str = "disabled") -> Settings:
    return Settings(
        youtube_api_key="fake-key",
        public_base_url="https://mcp.example.com",
        cf_access_team_domain="team.cloudflareaccess.com" if auth_mode == "cloudflare" else "",
        cf_access_audiences=("audience",) if auth_mode == "cloudflare" else (),
        auth_mode=auth_mode,
        cache_dir=tmp_path / "cache",
        model_cache_dir=tmp_path / "models",
    )


def test_registers_exactly_five_tools(tmp_path):
    app = create_app(_settings(tmp_path))
    tools = asyncio.run(app.state.mcp.list_tools())
    assert {tool.name for tool in tools} == {
        "get_video_details",
        "get_transcript",
        "transcribe",
        "search_videos",
        "materialize_video",
    }


@pytest.mark.parametrize(
    ("output", "has_text", "has_segments"),
    [("text", True, False), ("segments", False, True), ("both", True, True)],
)
def test_selects_transcript_output(output, has_text, has_segments):
    result = TranscriptResult(
        video_id="dQw4w9WgXcQ",
        available=True,
        text="hello",
        segments=[TranscriptSegment(start=0.0, end=1.0, duration=1.0, text="hello")],
    )

    payload = _select_transcript_output(result, output)

    assert ("text" in payload) is has_text
    assert ("segments" in payload) is has_segments
    assert payload["video_id"] == "dQw4w9WgXcQ"


def test_transcript_tools_expose_output_schema(tmp_path):
    app = create_app(_settings(tmp_path))
    tools = {tool.name: tool for tool in asyncio.run(app.state.mcp.list_tools())}

    for name in ("get_transcript", "transcribe"):
        schema = tools[name].inputSchema["properties"]["output"]
        assert schema["default"] == "text"
        assert set(schema["enum"]) == {"text", "segments", "both"}


@pytest.mark.asyncio
async def test_streamable_http_initialize(tmp_path):
    app = create_app(_settings(tmp_path))
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1"},
        },
    }
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            response = await client.post(
                "/mcp",
                json=request,
                headers={"Accept": "application/json, text/event-stream"},
            )

    assert response.status_code == 200
    assert "youtube" in response.text


@pytest.mark.asyncio
async def test_cloudflare_mode_rejects_missing_assertion(tmp_path):
    app = create_app(_settings(tmp_path, "cloudflare"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        response = await client.post("/mcp", json={})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_materialize_returns_resource_link_and_progress(tmp_path):
    app = create_app(_settings(tmp_path))
    now = datetime.now(timezone.utc)
    app.state.artifact_store.materialize = AsyncMock(
        return_value=ArtifactMetadata(
            artifact_id="a" * 64,
            video_id="dQw4w9WgXcQ",
            canonical_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            filename="video.mp4",
            size=10,
            max_height=720,
            download_url="https://mcp.example.com/artifacts/example",
            created_at=now.isoformat(),
            expires_at=(now + timedelta(hours=1)).isoformat(),
        )
    )
    request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "materialize_video",
            "arguments": {"video": "dQw4w9WgXcQ"},
            "_meta": {"progressToken": "test"},
        },
    }
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            response = await client.post(
                "/mcp",
                json=request,
                headers={"Accept": "application/json, text/event-stream"},
            )

    assert response.status_code == 200
    assert '"type":"resource_link"' in response.text
    assert '"progressToken":"test"' in response.text
    assert "https://mcp.example.com/artifacts/example" in response.text
