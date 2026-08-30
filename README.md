# YouTube MCP

This project provides a private Streamable HTTP MCP server for YouTube.

The server uses the official YouTube Data API for metadata and search. It uses YouTube caption tracks for fast transcript requests.

Local speech recognition is a separate operation. It uses `yt-dlp` and `faster-whisper` only when the client calls `transcribe`.

## Tools

| Tool | Purpose |
|---|---|
| `get_video_details` | Get video metadata from the YouTube Data API. |
| `get_transcript` | Get an existing manual or generated caption track. |
| `transcribe` | Download audio and run local speech recognition. |
| `search_videos` | Search YouTube with the YouTube Data API. |
| `materialize_video` | Create a downloadable MP4 artifact with a maximum height of 720p. |

All video tools accept a raw video ID or a normal YouTube video URL.

`get_transcript` never calls Whisper. If no matching caption track exists, the tool returns `available: false`.

`materialize_video` creates a user download. It does not send the video to Whisper or ask the client to analyze it.

## Requirements

- Python 3.11 or later for local development.
- A YouTube Data API v3 key.
- FFmpeg for local video materialization.
- An amd64 processor with AVX2 for the supplied container image.
- Cloudflare Tunnel and Cloudflare Access for production use.

The server targets CPU transcription. The default configuration uses `small.en`, `small`, and INT8 computation.

## Local development

Create a Python environment:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

Create the local environment file:

```bash
cp .env.example .env
```

Put the YouTube API key in `.env`. Keep `AUTH_MODE=disabled` and `HOST=127.0.0.1` for local work only.

Start the server:

```bash
.venv/bin/youtube-mcp
```

The MCP endpoint is `http://127.0.0.1:8000/mcp`.

Run the test suite:

```bash
.venv/bin/python -m pytest -q
```

The tests do not use YouTube, Cloudflare, or model downloads.

## Configuration

The server reads these environment variables at startup:

| Variable | Default | Purpose |
|---|---:|---|
| `YOUTUBE_API_KEY` | Optional | YouTube Data API v3 key for `get_video_details` and `search_videos`. |
| `PUBLIC_BASE_URL` | `http://127.0.0.1:8000` | Public server URL without `/mcp`. |
| `AUTH_MODE` | `cloudflare` | Use `cloudflare` in production or `disabled` on loopback for local work. |
| `CF_ACCESS_TEAM_DOMAIN` | Required in production | Cloudflare Access team domain. |
| `CF_ACCESS_AUDIENCE` | Required in production | One or more comma-separated Access audience tags. |
| `HOST` | `127.0.0.1` | Server bind address. The container sets `0.0.0.0`. |
| `PORT` | `8000` | Server port. |
| `CACHE_DIR` | `/data/cache` | Audio and video cache directory. |
| `MODEL_CACHE_DIR` | `/data/models` | Persistent faster-whisper model directory. |
| `MAX_VIDEO_DURATION_SECONDS` | `3600` | Maximum source duration for downloads and transcription. |
| `LONG_OPERATION_TIMEOUT_SECONDS` | `7200` | Server timeout for one long tool call. |
| `CACHE_TTL_SECONDS` | `86400` | Cache lifetime in seconds. |
| `CACHE_MAX_BYTES` | `53687091200` | Total media cache size. The default is 50 GiB. |
| `ARTIFACT_MAX_BYTES` | `8589934592` | Maximum video artifact size. The default is 8 GiB. |
| `WHISPER_EN_MODEL` | `small.en` | English faster-whisper checkpoint. |
| `WHISPER_MULTILINGUAL_MODEL` | `small` | Multilingual faster-whisper checkpoint. |
| `WHISPER_COMPUTE_TYPE` | `int8` | CTranslate2 computation type. |
| `WHISPER_CPU_THREADS` | `4` | CPU threads for one transcription. |
| `WHISPER_MODEL_IDLE_SECONDS` | `300` | Time before the server removes an idle model from memory. |
| `WHISPER_BEAM_SIZE` | `5` | Beam size for speech recognition. |

The first call to `transcribe` downloads the selected model. The server keeps only one model in memory.

The server removes the model from memory after the idle period. The model files remain in `MODEL_CACHE_DIR`.

## Cloudflare Access

Cloudflare handles user or service authentication before the request reaches this server.

The server validates the `Cf-Access-Jwt-Assertion` header on every HTTP request. It validates the signature, issuer, audience, and expiration.

Configure the Cloudflare Tunnel to forward the public hostname to `http://127.0.0.1:8000`.

Set these production values:

```dotenv
AUTH_MODE=cloudflare
PUBLIC_BASE_URL=https://youtube-mcp.example.com
CF_ACCESS_TEAM_DOMAIN=your-team.cloudflareaccess.com
CF_ACCESS_AUDIENCE=your-application-audience-tag
```

Do not expose the origin port on a public interface. A public origin lets an attacker send a forged assertion header directly.

Add `https://youtube-mcp.example.com/mcp` as the remote MCP URL in ChatGPT. The configured Access policy must authenticate that connection.

Artifact links use the same hostname. Cloudflare Access also protects each artifact download.

## Container image

Build the image with Podman:

```bash
podman build -t youtube-mcp:local -f Containerfile .
```

Create the persistent directories:

```bash
mkdir -p "$HOME/.local/share/youtube-mcp/cache"
mkdir -p "$HOME/.local/share/youtube-mcp/models"
```

Run the image:

```bash
podman run --rm \
  --env-file deploy/youtube-mcp.env.example \
  --publish 127.0.0.1:8000:8000 \
  --volume "$HOME/.local/share/youtube-mcp/cache:/data/cache:Z,U" \
  --volume "$HOME/.local/share/youtube-mcp/models:/data/models:Z,U" \
  youtube-mcp:local
```

Copy the environment example before this command. Put real secrets in the copy.

The image runs as a non-root user. It does not contain API keys, Access values, media, or models.

## GHCR publishing

The GitHub Actions workflow runs the tests and builds a Linux amd64 image.

The workflow publishes these private GHCR tags from `main` and version tags:

- `latest` from the default branch.
- A commit SHA tag.
- Semantic version tags for Git tags such as `v1.2.3`.

Set the GHCR package visibility to private in GitHub. The workflow uses `GITHUB_TOKEN` and does not require runtime secrets.

## Rootless Quadlet deployment

Copy the environment file:

```bash
mkdir -p "$HOME/.config/youtube-mcp"
cp deploy/youtube-mcp.env.example "$HOME/.config/youtube-mcp/youtube-mcp.env"
chmod 600 "$HOME/.config/youtube-mcp/youtube-mcp.env"
```

Edit the copied file. Add the production URL, YouTube key, team domain, and audience.

Create the persistent directories:

```bash
mkdir -p "$HOME/.local/share/youtube-mcp/cache"
mkdir -p "$HOME/.local/share/youtube-mcp/models"
```

Copy the Quadlet file:

```bash
mkdir -p "$HOME/.config/containers/systemd"
cp deploy/youtube-mcp.container "$HOME/.config/containers/systemd/"
```

Replace `OWNER` in the copied Quadlet file with the GitHub owner. Then log in to GHCR with an account that can pull the package.

Start the service:

```bash
systemctl --user daemon-reload
systemctl --user enable --now youtube-mcp.service
```

Enable user lingering if the service must start without an interactive login:

```bash
loginctl enable-linger "$USER"
```

Read the service log:

```bash
journalctl --user -u youtube-mcp.service -f
```

Pull and restart for a manual update:

```bash
podman pull ghcr.io/OWNER/youtube-mcp:latest
systemctl --user restart youtube-mcp.service
```

The Quadlet also sets `AutoUpdate=registry`. Enable the Podman user auto-update timer if you want automatic updates.

## Cache behavior

The server caches downloaded audio and materialized videos. It removes expired files and the oldest files above the total limit.

A completed artifact remains available across container restarts. Its MCP result includes a resource link and a normal download URL.

The server removes partial files after an error, cancellation, or timeout. `yt-dlp` errors include a short diagnostic message.

## License notice

The upstream project does not include a license file. This public fork does not add or imply redistribution rights. Obtain permission before you redistribute the code.
