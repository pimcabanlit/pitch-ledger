"""Producer identity for the API.

Identity always comes from a verified token, never from the request body. In ``entra``
mode the bearer token must be a Microsoft Entra ID access token for this API (audience),
from our tenant (issuer), signed by Entra (keys from the tenant's JWKS endpoint), and
carrying the producer app role. Missing settings refuse every producer call (fail closed).
``dev`` mode returns a fixed local producer and is for a developer's own machine.
"""

from dataclasses import dataclass
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from pitch_ledger.api.settings import ApiSettings

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Producer:
    producer_id: str  # stable subject (Entra object ID), recorded as approved_by
    name: str


DEV_PRODUCER = Producer("producer_local", "Local producer (dev mode)")


class EntraVerifier:
    """Verifies Entra ID access tokens; ``jwks`` is replaceable in tests."""

    def __init__(self, tenant_id: str, audience: str, role: str, jwks: Any = None) -> None:
        self.issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self.audience, self.role = audience, role
        self.jwks = jwks or jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        )

    def verify(self, token: str) -> Producer:
        try:
            key = self.jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc
        if self.role not in claims.get("roles", []):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "producer role required")
        return Producer(str(claims.get("oid") or claims["sub"]), str(claims.get("name", "")))


def producer(
    request: Request, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> Producer:
    settings: ApiSettings = request.app.state.settings
    if settings.api_auth == "dev":
        return DEV_PRODUCER
    verifier: EntraVerifier | None = request.app.state.verifier
    if verifier is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "producer sign-in not configured")
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sign in as a producer")
    return verifier.verify(creds.credentials)
