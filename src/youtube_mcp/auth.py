from collections.abc import Awaitable, Callable
from typing import Any

import jwt
from jwt import PyJWKClient


class CloudflareJWTVerifier:
    def __init__(self, issuer: str, audiences: tuple[str, ...]) -> None:
        self._issuer = issuer.rstrip("/")
        self._audiences = audiences
        self._jwks = PyJWKClient(f"{self._issuer}/cdn-cgi/access/certs")

    def verify(self, token: str) -> dict[str, Any]:
        signing_key = self._jwks.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=list(self._audiences),
            issuer=self._issuer,
            options={"require": ["exp", "iss", "aud"]},
        )


class CloudflareAccessMiddleware:
    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        verifier: CloudflareJWTVerifier | None,
    ) -> None:
        self._app = app
        self._verifier = verifier

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http" or self._verifier is None:
            await self._app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        token = headers.get(b"cf-access-jwt-assertion", b"").decode("ascii", errors="ignore")
        try:
            if not token:
                raise jwt.InvalidTokenError("missing assertion")
            claims = self._verifier.verify(token)
        except (jwt.PyJWTError, ValueError, UnicodeError):
            await self._reject(send)
            return

        scope["cf_access_claims"] = claims
        await self._app(scope, receive, send)

    @staticmethod
    async def _reject(send: Callable) -> None:
        body = b'{"error":"A valid Cloudflare Access assertion is required."}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
