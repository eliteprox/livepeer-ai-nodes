"""In-process ComfyPeer/PymtHouse account session for ComfyUI.

User and signer JWTs never leave this process.  The frontend receives only
device-flow state and the account's own usage data.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from .pymthouse_auth import (
    DeviceAuthorization,
    PymtHouseAuthError,
    PymtHouseClient,
    expires_at_from,
    format_usage_summary,
    load_runtime_settings,
    set_bearer,
)

LOGGER = logging.getLogger("comfyui_trickle.pymthouse")

USER_TOKEN_TTL_DEFAULT = 3600.0
SIGNER_TOKEN_TTL_DEFAULT = 300.0
REFRESH_SKEW_S = 60.0
REFRESH_POLL_S = 15.0
# Public catalog, one row per app. Runner metadata and URLs stay on the discovery inventory.
OFFERINGS_URL = "https://engine-preview.up.railway.app/v1/offerings"
DISCOVERY_URL_OVERRIDE = "https://discovery-preview.up.railway.app/discovery"


@dataclass
class _LoginState:
    status: str = "idle"
    user_code: str = ""
    verification_uri_complete: str = ""
    message: str = "Not signed in"
    usage_summary: str = ""
    usage: dict[str, object] = field(default_factory=dict)
    balance: dict[str, object] = field(default_factory=dict)
    access_token: str = ""
    refresh_token: str = ""
    access_expires_at: float = 0.0
    signer_expires_at: float = 0.0
    signer_url: str = ""
    discovery_url: str = ""
    signer_headers: dict[str, str] = field(default_factory=dict)


class _AuthLoop:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(
            target=self.loop.run_forever,
            name="pymthouse-auth",
            daemon=True,
        )
        self.thread.start()

    def run(self, coro, timeout: float):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=timeout)

    async def await_coro(self, coro):
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, self.loop))


class LoginSession:
    def __init__(
        self,
        client: Optional[PymtHouseClient] = None,
    ):
        self._client = client
        self._state = _LoginState()
        self._lock = threading.Lock()
        self._runtime: Optional[_AuthLoop] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._refresh_task: Optional[asyncio.Task] = None

    def public_status(self) -> dict[str, object]:
        with self._lock:
            pending = self._state.status == "pending"
            return {
                "status": self._state.status,
                "signed_in": self._state.status == "signed_in",
                "user_code": self._state.user_code if pending else "",
                "verification_uri_complete": self._state.verification_uri_complete if pending else "",
                "message": self._state.message,
                "usage_summary": self._state.usage_summary,
                # These are the signed-in user's own account records.  Do not
                # add access_token, refresh_token, signer_headers, or any
                # other credential to this local-UI response.
                "usage": dict(self._state.usage),
                "balance": dict(self._state.balance),
            }

    def verification_redirect(self) -> str:
        with self._lock:
            if self._state.status != "pending":
                return ""
            return self._state.verification_uri_complete

    def signer_material(self) -> Optional[tuple[str, dict[str, str]]]:
        with self._lock:
            if self._state.status != "signed_in":
                return None
            if not self._state.signer_url or "Authorization" not in self._state.signer_headers:
                return None
            return self._state.signer_url, self._state.signer_headers

    async def begin_login(self) -> dict[str, object]:
        runtime = self._ensure_runtime()
        return await runtime.await_coro(self._begin_login())

    async def discovery_request(self) -> tuple[str, dict[str, str]]:
        """Discovery URL and signer headers for the signed-in session.

        The headers stay in this process. Callers must not send them to the browser.
        """
        runtime = self._ensure_runtime()
        return await runtime.await_coro(self._discovery_request())

    async def refresh_usage(self) -> dict[str, object]:
        """Refresh the signed-in user's usage without exposing their JWT."""
        runtime = self._ensure_runtime()
        return await runtime.await_coro(self._refresh_usage())

    async def sign_out(self) -> dict[str, object]:
        """Forget all local credentials and stop background account work."""
        runtime = self._ensure_runtime()
        return await runtime.await_coro(self._sign_out())

    def refresh_for_stream(self, timeout: float = 20.0) -> Optional[tuple[str, dict[str, str]]]:
        if self.public_status()["status"] != "signed_in":
            return None
        runtime = self._ensure_runtime()
        runtime.run(self._ensure_fresh(), timeout=timeout)
        return self.signer_material()

    def _ensure_runtime(self) -> _AuthLoop:
        if self._runtime is None:
            self._runtime = _AuthLoop()
        return self._runtime

    def _client_or_create(self) -> PymtHouseClient:
        if self._client is None:
            self._client = PymtHouseClient(load_runtime_settings(os.environ))
        return self._client

    async def _begin_login(self) -> dict[str, object]:
        await self._discard_session(status="pending", message="Starting secure sign-in…")
        try:
            client = self._client_or_create()
            device = await client.start_device_login()
        except PymtHouseAuthError as exc:
            self._fail(exc.message)
            raise
        except Exception:
            LOGGER.exception("ComfyPeer device login could not be started")
            self._fail("Could not start sign-in.")
            raise
        with self._lock:
            self._state.status = "pending"
            self._state.user_code = device.user_code
            self._state.verification_uri_complete = device.verification_uri_complete
            self._state.message = f"Enter {device.user_code} in the browser to finish sign-in."
            self._state.usage_summary = ""
        LOGGER.info("ComfyPeer device login started user_code=%s", device.user_code)
        self._poll_task = asyncio.create_task(self._poll_until_approved(device), name="pymthouse-device-poll")
        return self.public_status()

    async def _poll_until_approved(self, device: DeviceAuthorization) -> None:
        client = self._client_or_create()
        try:
            token = await client.poll_device_token(device)
            self._store_user_token(token)
            await self._exchange_signer()
            await self._load_usage()
            with self._lock:
                self._state.status = "signed_in"
                self._state.user_code = ""
                self._state.verification_uri_complete = ""
                summary = self._state.usage_summary
                self._state.message = f"Signed in. {summary}".strip()
            self._refresh_task = asyncio.create_task(self._refresh_loop(), name="pymthouse-refresh")
            LOGGER.info("ComfyPeer sign-in complete")
        except PymtHouseAuthError as exc:
            self._fail(exc.message)
        except Exception:
            LOGGER.exception("PymtHouse device poll failed")
            self._fail("Sign-in failed.")

    async def _refresh_loop(self) -> None:
        while self.public_status()["status"] == "signed_in":
            await asyncio.sleep(REFRESH_POLL_S)
            if self.public_status()["status"] != "signed_in":
                return
            try:
                await self._ensure_fresh()
            except PymtHouseAuthError as exc:
                LOGGER.warning("PymtHouse refresh failed: %s", exc.message)
                with self._lock:
                    if self._state.signer_expires_at <= time.monotonic():
                        self._state.status = "error"
                        self._state.message = exc.message
                        return
                    self._state.message = exc.message

    async def _ensure_fresh(self) -> None:
        client = self._client_or_create()
        now = time.monotonic()
        with self._lock:
            access_expires_at = self._state.access_expires_at
            refresh_token = self._state.refresh_token
            signer_expires_at = self._state.signer_expires_at
            access_token = self._state.access_token
        if refresh_token and access_expires_at - now <= REFRESH_SKEW_S:
            token = await client.refresh_access_token(refresh_token)
            self._store_user_token(token)
            with self._lock:
                access_token = self._state.access_token
        if signer_expires_at - now <= REFRESH_SKEW_S and access_token:
            await self._exchange_signer()

    async def _discovery_request(self) -> tuple[str, dict[str, str]]:
        if self.public_status()["status"] != "signed_in":
            raise PymtHouseAuthError("Sign in to your ComfyPeer account first.")
        await self._ensure_fresh()
        with self._lock:
            missing = not self._state.discovery_url and bool(self._state.access_token)
        if missing:
            await self._exchange_signer()
        with self._lock:
            url = DISCOVERY_URL_OVERRIDE or self._state.discovery_url
            headers = dict(self._state.signer_headers)
        if not url:
            raise PymtHouseAuthError("Signer session did not include a discovery URL.")
        return url, headers

    async def _refresh_usage(self) -> dict[str, object]:
        if self.public_status()["status"] != "signed_in":
            raise PymtHouseAuthError("Sign in to your ComfyPeer account first.")
        await self._ensure_fresh()
        await self._load_usage()
        with self._lock:
            summary = self._state.usage_summary
            self._state.message = f"Signed in. {summary}".strip()
        return self.public_status()

    async def _sign_out(self) -> dict[str, object]:
        await self._discard_session(status="idle", message="Signed out")
        LOGGER.info("ComfyPeer account signed out and local tokens were cleared")
        return self.public_status()

    async def _discard_session(self, *, status: str, message: str) -> None:
        """Cancel auth work, clear credentials, and retire the HTTP client."""
        self._cancel_task(self._poll_task)
        self._cancel_task(self._refresh_task)
        self._poll_task = None
        self._refresh_task = None
        with self._lock:
            client = self._client
            self._client = None
            # The gateway holds this dictionary by reference while a stream is
            # active.  Clearing it prevents a signed-out account from being
            # used for a subsequent paid request.
            self._state.signer_headers.clear()
            self._state = _LoginState(status=status, message=message)
        if client is not None:
            try:
                await client.close()
            except Exception:  # pragma: no cover - cleanup must not block logout
                LOGGER.debug("Could not close PymtHouse HTTP client", exc_info=True)

    async def _exchange_signer(self) -> None:
        client = self._client_or_create()
        with self._lock:
            access_token = self._state.access_token
            headers = self._state.signer_headers
        if not access_token:
            raise PymtHouseAuthError("Sign in before starting a paid stream.")
        session = await client.exchange_signer_session(access_token)
        signer_token = str(session["access_token"])
        signer_url = session.get("signer_url")
        set_bearer(headers, signer_token)
        with self._lock:
            if isinstance(signer_url, str) and signer_url.strip():
                self._state.signer_url = signer_url.strip()
            self._state.discovery_url = discovery_url_from_session(
                session.get("discovery_url"),
                self._state.signer_url,
            )
            self._state.signer_expires_at = expires_at_from(
                session.get("expires_in"),
                now=time.monotonic(),
                default_seconds=SIGNER_TOKEN_TTL_DEFAULT,
            )

    async def _load_usage(self) -> None:
        client = self._client_or_create()
        with self._lock:
            access_token = self._state.access_token
        try:
            usage, balance = await client.fetch_usage(access_token)
        except PymtHouseAuthError as exc:
            LOGGER.info("ComfyPeer usage read failed: %s", exc.message)
            with self._lock:
                self._state.usage_summary = "usage unavailable"
                self._state.usage = {}
                self._state.balance = {}
            return
        summary = format_usage_summary(usage, balance)
        with self._lock:
            self._state.usage_summary = summary
            self._state.usage = usage
            self._state.balance = balance

    def _store_user_token(self, token: dict) -> None:
        access_token = token.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise PymtHouseAuthError("Token response did not include an access token.")
        refresh_token = token.get("refresh_token")
        with self._lock:
            self._state.access_token = access_token
            if isinstance(refresh_token, str) and refresh_token:
                self._state.refresh_token = refresh_token
            self._state.access_expires_at = expires_at_from(
                token.get("expires_in"),
                now=time.monotonic(),
                default_seconds=USER_TOKEN_TTL_DEFAULT,
            )

    def _fail(self, message: str) -> None:
        with self._lock:
            self._state.status = "error"
            self._state.message = message
            self._state.user_code = ""
            self._state.verification_uri_complete = ""
            self._state.access_token = ""
            self._state.refresh_token = ""
            self._state.access_expires_at = 0.0
            self._state.signer_expires_at = 0.0
            self._state.signer_url = ""
            self._state.discovery_url = ""
            self._state.signer_headers.clear()
        LOGGER.info("ComfyPeer sign-in error: %s", message)

    @staticmethod
    def _cancel_task(task: Optional[asyncio.Task]) -> None:
        if task is not None and not task.done():
            task.cancel()


_SESSION: Optional[LoginSession] = None
_SESSION_LOCK = threading.Lock()


def get_login_session() -> LoginSession:
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            _SESSION = LoginSession()
        return _SESSION


def discovery_url_from_session(discovery_url: object, signer_url: str) -> str:
    """Prefer the session's discovery_url, otherwise `{signer}/discover-orchestrators`."""
    if isinstance(discovery_url, str) and discovery_url.strip():
        return _http_url(discovery_url)
    if signer_url.strip():
        return discover_orchestrators_url(signer_url)
    return ""


def discover_orchestrators_url(signer_url: str) -> str:
    parsed = urlparse(_http_url(signer_url))
    path = parsed.path.rstrip("/")
    if path == "/":
        path = ""
    return f"{parsed.scheme}://{parsed.netloc}{path}/discover-orchestrators"


def _http_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise PymtHouseAuthError("Signer session discovery URL is invalid.")
    return url.strip()


def stream_signer(timeout: float = 20.0) -> Optional[tuple[str, dict[str, str]]]:
    """Signer URL and mutable bearer headers when a PymtHouse session is active."""
    return get_login_session().refresh_for_stream(timeout=timeout)
