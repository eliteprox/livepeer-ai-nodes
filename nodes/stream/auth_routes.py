from __future__ import annotations

import asyncio
import logging
import threading

from aiohttp import web
from livepeer_gateway.oidc_auth import clear_cached_token, ensure_valid_token
from server import PromptServer

from .credentials import BILLING_URL, CLIENT_ID, OIDC_SCOPES, cached_auth_status

LOGGER = logging.getLogger("comfyui_trickle.auth_routes")

_AUTH_LOCK = threading.Lock()
_AUTH_URL_READY = threading.Event()
_AUTH_STATE = {
    "in_progress": False,
    "auth_url": "",
    "user_code": "",
    "expires_in": 0,
    "error": "",
}


def _on_device_auth(auth_url: str, user_code: str, expires_in: int) -> None:
    with _AUTH_LOCK:
        _AUTH_STATE["auth_url"] = auth_url
        _AUTH_STATE["user_code"] = user_code
        _AUTH_STATE["expires_in"] = int(expires_in)
    _AUTH_URL_READY.set()
    try:
        PromptServer.instance.send_sync(
            "livepeer_device_auth_required",
            {
                "auth_url": auth_url,
                "user_code": user_code,
                "expires_in": expires_in,
                "workflow_interrupted": False,
            },
        )
    except Exception:
        LOGGER.debug("Failed to send device auth event", exc_info=True)


def _run_device_login_worker() -> None:
    try:
        ensure_valid_token(
            BILLING_URL,
            client_id=CLIENT_ID,
            scopes=OIDC_SCOPES,
            headless=True,
            on_device_auth=_on_device_auth,
        )
        with _AUTH_LOCK:
            _AUTH_STATE["error"] = ""
    except Exception as exc:
        LOGGER.exception("Livepeer device login failed")
        with _AUTH_LOCK:
            _AUTH_STATE["error"] = str(exc)
    finally:
        with _AUTH_LOCK:
            _AUTH_STATE["in_progress"] = False
            if not _AUTH_STATE["auth_url"]:
                _AUTH_URL_READY.set()


def _auth_status_payload() -> dict:
    authenticated, reason = cached_auth_status()
    with _AUTH_LOCK:
        in_progress = bool(_AUTH_STATE["in_progress"])
        auth_url = str(_AUTH_STATE["auth_url"])
        user_code = str(_AUTH_STATE["user_code"])
        expires_in = int(_AUTH_STATE["expires_in"])
        login_error = str(_AUTH_STATE["error"])

    return {
        "ok": True,
        "authenticated": authenticated,
        "reason": reason,
        "login_in_progress": in_progress,
        "auth_url": auth_url,
        "user_code": user_code,
        "expires_in": expires_in,
        "login_error": login_error,
    }


@PromptServer.instance.routes.get("/livepeer/auth/status")
async def livepeer_auth_status(_request: web.Request) -> web.Response:
    return web.json_response(_auth_status_payload())


@PromptServer.instance.routes.post("/livepeer/auth/login")
async def livepeer_auth_login(_request: web.Request) -> web.Response:
    try:
        authenticated, _ = cached_auth_status()
        if authenticated:
            return web.json_response(
                {
                    "ok": True,
                    "authenticated": True,
                    "pending": False,
                }
            )

        start_worker = False
        with _AUTH_LOCK:
            if not _AUTH_STATE["in_progress"]:
                _AUTH_STATE["in_progress"] = True
                _AUTH_STATE["auth_url"] = ""
                _AUTH_STATE["user_code"] = ""
                _AUTH_STATE["expires_in"] = 0
                _AUTH_STATE["error"] = ""
                _AUTH_URL_READY.clear()
                start_worker = True

        if start_worker:
            thread = threading.Thread(
                target=_run_device_login_worker,
                name="livepeer-device-login",
                daemon=True,
            )
            thread.start()

        await asyncio.to_thread(_AUTH_URL_READY.wait, 5.0)
        with _AUTH_LOCK:
            auth_url = str(_AUTH_STATE["auth_url"])
            user_code = str(_AUTH_STATE["user_code"])
            expires_in = int(_AUTH_STATE["expires_in"])
            login_error = str(_AUTH_STATE["error"])

        authenticated, _ = cached_auth_status()
        if authenticated:
            return web.json_response(
                {
                    "ok": True,
                    "authenticated": True,
                    "pending": False,
                }
            )

        if login_error and not auth_url:
            return web.json_response(
                {
                    "ok": False,
                    "error": login_error,
                },
                status=500,
            )

        return web.json_response(
            {
                "ok": True,
                "authenticated": False,
                "pending": True,
                "auth_url": auth_url,
                "user_code": user_code,
                "expires_in": expires_in,
            }
        )
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.exception("Livepeer login initialization failed")
        return web.json_response(
            {
                "ok": False,
                "error": str(exc),
            },
            status=500,
        )


@PromptServer.instance.routes.post("/livepeer/auth/logout")
async def livepeer_auth_logout(_request: web.Request) -> web.Response:
    try:
        await asyncio.to_thread(
            clear_cached_token,
            BILLING_URL,
            client_id=CLIENT_ID,
            scopes=OIDC_SCOPES,
        )
        return web.json_response(
            {
                "ok": True,
                "authenticated": False,
            }
        )
    except Exception as exc:
        LOGGER.exception("Livepeer logout failed")
        return web.json_response(
            {
                "ok": False,
                "error": str(exc),
            },
            status=500,
        )
