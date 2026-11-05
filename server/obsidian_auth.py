"""Gates the snippet-submission endpoint behind an Obsidian access token, so
only pre-authorized devices (mTLS client cert -> Obsidian JWT) can submit
snippets. Verifies the JWT's signature against Obsidian's JWKS locally — no
network call back to Obsidian on every request.

See https://github.com/jyjulianwong/Obsidian-Server for the auth service
itself and examples/fastapi_verify_token.py for the canonical version of
this adapter.
"""

import os

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

OBSIDIAN_ISSUER = os.environ.get("OBSIDIAN_ISSUER", "https://auth.jyjwong.com")
# Deliberately a separate domain from OBSIDIAN_ISSUER, not a path under it:
# auth.jyjwong.com requires a client certificate for every route (API
# Gateway enforces mTLS per-domain, not per-route), so a resource server
# with no device cert of its own — like this one — gets a TLS-level
# connection reset trying to fetch JWKS from there. jwks.jyjwong.com is a
# separate, non-mTLS API Gateway domain that serves only that route.
_JWKS_URL = os.environ.get("OBSIDIAN_JWKS_URL", "https://jwks.jyjwong.com/.well-known/jwks.json")

# PyJWKClient caches keys in-memory and only re-fetches the JWKS when it sees
# an unrecognized `kid` — cheap enough to construct once at import time.
_jwk_client = PyJWKClient(_JWKS_URL)


def _verify_bearer(authorization: str) -> str:
    """Validates a `Bearer <jwt>` header value; returns the device_id or raises 401."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ")

    try:
        signing_key = _jwk_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=OBSIDIAN_ISSUER,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc

    return payload["sub"]  # device_id


def require_device(authorization: str = Header(...)) -> str:
    """FastAPI dependency: returns the authenticated device_id, or raises 401."""
    return _verify_bearer(authorization)


def optional_device(authorization: str | None = Header(default=None)) -> str | None:
    """FastAPI dependency for routes that are public but unlock more for an
    authenticated device: returns the device_id, or None when no
    `Authorization` header is sent. A header that *is* sent but invalid still
    raises 401 — a caller presenting a bad/expired token should be told so
    rather than silently treated as anonymous.
    """
    if authorization is None:
        return None
    return _verify_bearer(authorization)
