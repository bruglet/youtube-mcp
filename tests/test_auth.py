from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from youtube_mcp.auth import CloudflareAccessMiddleware, CloudflareJWTVerifier


@pytest.fixture
def keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


def _token(private, **overrides):
    now = datetime.now(timezone.utc)
    claims = {
        "iss": "https://team.cloudflareaccess.com",
        "aud": ["audience"],
        "sub": "user",
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": "test"})


def _verifier(public):
    verifier = CloudflareJWTVerifier(
        "https://team.cloudflareaccess.com", ("audience",)
    )
    verifier._jwks = SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=public)
    )
    return verifier


def test_valid_access_assertion(keys):
    private, public = keys
    claims = _verifier(public).verify(_token(private))
    assert claims["sub"] == "user"


def test_rejects_wrong_audience_and_expiry(keys):
    private, public = keys
    verifier = _verifier(public)
    with pytest.raises(jwt.InvalidAudienceError):
        verifier.verify(_token(private, aud=["wrong"]))
    with pytest.raises(jwt.ExpiredSignatureError):
        verifier.verify(
            _token(private, exp=datetime.now(timezone.utc) - timedelta(seconds=1))
        )


@pytest.mark.asyncio
async def test_middleware_rejects_missing_assertion():
    async def endpoint(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/", endpoint)])
    app.add_middleware(
        CloudflareAccessMiddleware,
        verifier=SimpleNamespace(verify=lambda token: {"sub": "user"}),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_middleware_accepts_verified_assertion():
    async def endpoint(request):
        return PlainTextResponse(request.scope["cf_access_claims"]["sub"])

    app = Starlette(routes=[Route("/", endpoint)])
    app.add_middleware(
        CloudflareAccessMiddleware,
        verifier=SimpleNamespace(verify=lambda token: {"sub": "user"}),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/", headers={"Cf-Access-Jwt-Assertion": "token"}
        )
    assert response.status_code == 200
    assert response.text == "user"
