"""Local account routes for the ComfyPeer device-flow control."""

from __future__ import annotations

import logging

from .pymthouse_auth import PymtHouseAuthError
from .pymthouse_session import get_login_session

LOGGER = logging.getLogger("comfyui_trickle.pymthouse_login")


def register_login_routes() -> None:
    if getattr(register_login_routes, "_registered", False):
        return
    try:
        from aiohttp import web
        from server import PromptServer
    except ImportError:
        return
    instance = getattr(PromptServer, "instance", None)
    routes = getattr(instance, "routes", None)
    if routes is None:
        return

    @routes.post("/livepeer-ai-nodes/account/login")
    @routes.post("/livepeer-ai-nodes/pymthouse/login")
    async def start_login(_request):
        try:
            payload = await get_login_session().begin_login()
        except PymtHouseAuthError as exc:
            return web.json_response(_error_payload(exc.message), status=400)
        except Exception:
            LOGGER.exception("ComfyPeer login route failed")
            return web.json_response(_error_payload("Sign-in failed."), status=500)
        return web.json_response(payload)

    @routes.get("/livepeer-ai-nodes/account/status")
    @routes.get("/livepeer-ai-nodes/pymthouse/status")
    async def login_status(_request):
        return web.json_response(get_login_session().public_status())

    @routes.post("/livepeer-ai-nodes/account/usage")
    async def refresh_usage(_request):
        try:
            payload = await get_login_session().refresh_usage()
        except PymtHouseAuthError as exc:
            return web.json_response(_error_payload(exc.message), status=400)
        except Exception:
            LOGGER.exception("ComfyPeer usage refresh route failed")
            return web.json_response(_error_payload("Could not refresh usage."), status=500)
        return web.json_response(payload)

    @routes.post("/livepeer-ai-nodes/account/logout")
    async def logout(_request):
        return web.json_response(await get_login_session().sign_out())

    # Kept for clients on the prior extension; the global account control now
    # opens the issuer-provided device URL directly.
    @routes.get("/livepeer-ai-nodes/account/verification")
    @routes.get("/livepeer-ai-nodes/pymthouse/verification")
    async def open_verification(_request):
        target = get_login_session().verification_redirect()
        if not target:
            return web.Response(status=404, text="No sign-in in progress")
        raise web.HTTPFound(target)

    register_login_routes._registered = True


def _error_payload(message: str) -> dict[str, object]:
    return {
        "status": "error",
        "signed_in": False,
        "user_code": "",
        "verification_uri_complete": "",
        "message": message,
        "usage_summary": "",
        "usage": {},
        "balance": {},
    }


register_login_routes()

# Authentication is intentionally account-level UI, never a workflow node.
NODE_CLASS_MAPPINGS: dict[str, object] = {}
NODE_DISPLAY_NAME_MAPPINGS: dict[str, str] = {}
