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
_JWKS_URL = f"{OBSIDIAN_ISSUER}/.well-known/jwks.json"

# PyJWKClient caches keys in-memory and only re-fetches the JWKS when it sees
# an unrecognized `kid` — cheap enough to construct once at import time.
_jwk_client = PyJWKClient(_JWKS_URL)


def require_device(authorization: str = Header(...)) -> str:
    """FastAPI dependency: returns the authenticated device_id, or raises 401."""
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
