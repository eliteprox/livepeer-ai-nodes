"""PymtHouse public-client login, signer session, and usage reads."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Optional
from urllib.parse import quote, urlparse

from authlib.integrations.base_client import OAuthError
from authlib.integrations.httpx_client import AsyncOAuth2Client

DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
SIGN_IN_SCOPE = "openid sign:job"
ISSUER_SUFFIX = "/api/v1/oidc"

Sleep = Callable[[float], Awaitable[None]]


class PymtHouseAuthError(Exception):
    def __init__(
        self,
        message: str,
        error: str = "error",
    ):
        super().__init__(message)
        self.message = message
        self.error = error


@dataclass
class PymtHouseSettings:
    issuer_url: str
    client_id: str


@dataclass
class DeviceAuthorization:
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: float
    interval: float


def api_origin(issuer_url: str) -> str:
    """API origin for app and usage routes. Issuer is ``{origin}/api/v1/oidc``."""
    issuer = issuer_url.strip().rstrip("/")
    if issuer.endswith(ISSUER_SUFFIX):
        return issuer[: -len(ISSUER_SUFFIX)]
    parsed = urlparse(issuer)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise PymtHouseAuthError("PYMTHOUSE_ISSUER_URL must be an http(s) issuer URL.")
    return f"{parsed.scheme}://{parsed.netloc}"


def public_verification_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise PymtHouseAuthError("Issuer returned an invalid verification URL.")
    return parsed.geturl()


def same_origin_verification_url(verification_url: str, issuer_url: str) -> str:
    """Rebuild a device URL on the configured issuer origin.

    The path and query may come from the issuer response. The scheme and host
    always come from ``issuer_url``, so the browser is not sent to another site.
    """
    target = urlparse(public_verification_url(verification_url))
    issuer = urlparse(issuer_url.strip())
    if issuer.scheme not in {"https", "http"} or not issuer.netloc:
        raise PymtHouseAuthError("PYMTHOUSE_ISSUER_URL must be an http(s) issuer URL.")
    if target.scheme != issuer.scheme or target.netloc != issuer.netloc:
        raise PymtHouseAuthError("Verification URL is not on the PymtHouse issuer.")
    path = target.path or "/"
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        raise PymtHouseAuthError("Verification URL is not on the PymtHouse issuer.")
    query = f"?{target.query}" if target.query else ""
    return f"{issuer.scheme}://{issuer.netloc}{path}{query}"


def set_bearer(headers: dict[str, str], access_token: str) -> None:
    """Replace the bearer on the dict the gateway payment session already holds."""
    headers["Authorization"] = f"Bearer {access_token}"


def expires_at_from(expires_in: object, *, now: float, default_seconds: float) -> float:
    seconds = default_seconds
    if isinstance(expires_in, (int, float, str)):
        try:
            seconds = float(expires_in)
        except ValueError:
            seconds = default_seconds
    if seconds <= 0:
        seconds = default_seconds
    return now + seconds


def format_usage_summary(usage: Mapping[str, Any], balance: Mapping[str, Any]) -> str:
    totals = usage.get("totals")
    count = 0
    if isinstance(totals, Mapping):
        raw_count = totals.get("requestCount", 0)
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            count = 0
    remaining = balance.get("remainingUsdMicros")
    if remaining is None:
        remaining = balance.get("balanceUsdMicros")
    balance_text = "balance unavailable"
    if isinstance(remaining, int) or (isinstance(remaining, str) and remaining.lstrip("-").isdigit()):
        usd = int(remaining) / 1_000_000
        balance_text = f"${usd:.2f} remaining"
    requests = "1 request" if count == 1 else f"{count} requests"
    return f"{balance_text}, {requests}"


def load_settings_from_env(env: Mapping[str, str]) -> PymtHouseSettings:
    issuer_url = (env.get("PYMTHOUSE_ISSUER_URL") or "").strip()
    client_id = (env.get("PYMTHOUSE_CLIENT_ID") or "").strip()
    if not issuer_url or not client_id:
        raise PymtHouseAuthError(
            "Set PYMTHOUSE_ISSUER_URL and PYMTHOUSE_CLIENT_ID before signing in."
        )
    api_origin(issuer_url)
    return PymtHouseSettings(
        issuer_url=issuer_url.rstrip("/"),
        client_id=client_id,
    )


class PymtHouseClient:
    """Authlib client for a public ``app_`` OIDC client. No client secret."""

    def __init__(
        self,
        settings: PymtHouseSettings,
        http: Optional[AsyncOAuth2Client] = None,
    ):
        self.settings = settings
        self._http = http or AsyncOAuth2Client(
            client_id=settings.client_id,
            token_endpoint_auth_method="none",
            follow_redirects=False,
        )
        self._metadata: Optional[dict[str, Any]] = None

    @property
    def origin(self) -> str:
        return api_origin(self.settings.issuer_url)

    async def discover(self) -> dict[str, Any]:
        if self._metadata is not None:
            return self._metadata
        url = f"{self.settings.issuer_url}/.well-known/openid-configuration"
        response = await self._http.request("GET", url, withhold_token=True)
        payload = _json_object(response)
        if response.status_code >= 400:
            raise PymtHouseAuthError(
                "Could not read the PymtHouse OpenID configuration.",
                error="discovery_failed",
            )
        if not payload.get("device_authorization_endpoint") or not payload.get("token_endpoint"):
            raise PymtHouseAuthError(
                "OpenID configuration is missing the device or token endpoint.",
                error="discovery_failed",
            )
        self._metadata = payload
        return payload

    async def start_device_login(self) -> DeviceAuthorization:
        metadata = await self.discover()
        response = await self._http.request(
            "POST",
            str(metadata["device_authorization_endpoint"]),
            data={
                "client_id": self.settings.client_id,
                "scope": SIGN_IN_SCOPE,
                "resource": self.settings.issuer_url,
            },
            withhold_token=True,
        )
        payload = _json_object(response)
        _raise_for_oauth_payload(payload, response.status_code)
        verification = payload.get("verification_uri_complete") or payload.get("verification_uri")
        device_code = payload.get("device_code")
        user_code = payload.get("user_code")
        if not isinstance(device_code, str) or not isinstance(user_code, str) or not isinstance(verification, str):
            raise PymtHouseAuthError("Device authorization response was incomplete.")
        return DeviceAuthorization(
            device_code=device_code,
            user_code=user_code,
            verification_uri=str(payload.get("verification_uri") or verification),
            verification_uri_complete=same_origin_verification_url(
                verification,
                self.settings.issuer_url,
            ),
            expires_in=float(payload.get("expires_in") or 600),
            interval=float(payload.get("interval") or 5),
        )

    async def poll_device_token(
        self,
        device: DeviceAuthorization,
        *,
        sleep: Optional[Sleep] = None,
        now: Optional[Callable[[], float]] = None,
    ) -> dict[str, Any]:
        metadata = await self.discover()
        pause = sleep or _async_sleep
        clock = now or time.monotonic
        deadline = clock() + max(1.0, device.expires_in)
        wait = max(1.0, device.interval)
        token_endpoint = str(metadata["token_endpoint"])
        while True:
            if clock() >= deadline:
                raise PymtHouseAuthError(
                    "Device login expired before it was approved.",
                    error="expired_token",
                )
            try:
                token = await self._http.fetch_token(
                    token_endpoint,
                    grant_type=DEVICE_CODE_GRANT,
                    device_code=device.device_code,
                    resource=self.settings.issuer_url,
                )
            except OAuthError as exc:
                if exc.error == "authorization_pending":
                    await pause(wait)
                    continue
                if exc.error == "slow_down":
                    wait += 5
                    await pause(wait)
                    continue
                description = exc.description or exc.error or "Device login failed."
                raise PymtHouseAuthError(str(description), error=str(exc.error or "error")) from exc
            return dict(token)

    async def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        metadata = await self.discover()
        try:
            token = await self._http.refresh_token(
                str(metadata["token_endpoint"]),
                refresh_token=refresh_token,
                resource=self.settings.issuer_url,
            )
        except OAuthError as exc:
            description = exc.description or exc.error or "Refresh failed."
            raise PymtHouseAuthError(str(description), error=str(exc.error or "error")) from exc
        return dict(token)

    async def exchange_signer_session(self, subject_token: str) -> dict[str, Any]:
        client_id = quote(self.settings.client_id, safe="")
        url = f"{self.origin}/api/v1/apps/{client_id}/oidc/token"
        response = await self._http.request(
            "POST",
            url,
            data={
                "grant_type": TOKEN_EXCHANGE_GRANT,
                "subject_token": subject_token,
                "subject_token_type": ACCESS_TOKEN_TYPE,
            },
            withhold_token=True,
        )
        payload = _json_object(response)
        _raise_for_oauth_payload(payload, response.status_code)
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise PymtHouseAuthError("Signer session response did not include an access token.")
        return payload

    async def fetch_usage(self, access_token: str) -> tuple[dict[str, Any], dict[str, Any]]:
        headers = {"Authorization": f"Bearer {access_token}"}
        usage = await self._get_json(f"{self.origin}/api/v1/user/usage", headers)
        balance = await self._get_json(f"{self.origin}/api/v1/user/usage/balance", headers)
        return usage, balance

    async def _get_json(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        response = await self._http.request(
            "GET",
            url,
            headers=headers,
            withhold_token=True,
        )
        payload = _json_object(response)
        if response.status_code >= 400 or payload.get("error"):
            description = str(payload.get("error_description") or payload.get("error") or "Usage request failed.")
            raise PymtHouseAuthError(description, error=str(payload.get("error") or "usage_failed"))
        return payload


def _json_object(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception as exc:
        raise PymtHouseAuthError(
            f"PymtHouse returned a non-JSON response (HTTP {getattr(response, 'status_code', '?')})."
        ) from exc
    if not isinstance(payload, dict):
        raise PymtHouseAuthError("PymtHouse returned an unexpected JSON payload.")
    return payload


def _raise_for_oauth_payload(payload: Mapping[str, Any], status: int) -> None:
    if status < 400 and "error" not in payload:
        return
    error = str(payload.get("error") or "error")
    description = str(payload.get("error_description") or error)
    raise PymtHouseAuthError(description, error=error)


async def _async_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)
