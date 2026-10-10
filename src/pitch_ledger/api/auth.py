"""Producer identity for the API.

Identity always comes from a verified token, never from the request body. In ``entra``
mode the bearer token must be a Microsoft Entra ID access token for this API (audience),
signed by Entra, unexpired, and from an allowed issuer:

- ``role`` access (production): issued by our tenant and carrying the producer app role;
- ``any_account`` access (the public demo, so judges can try the console with their own
  Microsoft account): issued by the account's own tenant, work or personal; any signed-in
  account may act as a producer, and its object ID is recorded as the approver.

Missing settings refuse every producer call (fail closed). ``dev`` mode returns a fixed
local producer and is for a developer's own machine.
"""

import re
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


_TENANT_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_AUTHORITY = "https://login.microsoftonline.com"


class EntraVerifier:
    """Verifies Entra ID access tokens; ``jwks`` is replaceable in tests.

    ``tenant_id`` and ``role`` apply to ``role`` access; with ``any_account`` the issuer is
    the token's own tenant (``tid``) and no role is needed.
    """

    def __init__(
        self,
        tenant_id: str | None,
        audience: str,
        role: str,
        jwks: Any = None,
        any_account: bool = False,
    ) -> None:
        if not any_account and not tenant_id:
            raise ValueError("role access needs the tenant ID")
        self.issuer = f"{_AUTHORITY}/{tenant_id}/v2.0" if tenant_id else None
        self.audience, self.role, self.any_account = audience, role, any_account
        keys_tenant = "common" if any_account else tenant_id
        self.jwks = jwks or jwt.PyJWKClient(f"{_AUTHORITY}/{keys_tenant}/discovery/v2.0/keys")

    def verify(self, token: str) -> Producer:
        try:
            key = self.jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=None if self.any_account else self.issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc
        if self.any_account:
            tenant = str(claims.get("tid", ""))
            if not _TENANT_ID.match(tenant) or claims["iss"] != f"{_AUTHORITY}/{tenant}/v2.0":
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token issuer")
        elif self.role not in claims.get("roles", []):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "producer role required")
        name = claims.get("name") or claims.get("preferred_username") or ""
        return Producer(str(claims.get("oid") or claims["sub"]), str(name))


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
