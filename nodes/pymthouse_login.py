"""ComfyUI node and local routes for PymtHouse device sign-in."""

from __future__ import annotations

import logging
from typing import Any

from .pymthouse_auth import PymtHouseAuthError
from .pymthouse_session import get_login_session

LOGGER = logging.getLogger("comfyui_trickle.pymthouse_login")


class PymtHouseLogin:
    """
    Sign in with the public PymtHouse app client.

    The Sign in button lives in the frontend extension and calls the local
    routes registered below. Queueing this node reports the current status.
    """

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {"required": {}}

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("status", "user_code", "usage_summary")
    FUNCTION = "report"
    CATEGORY = "Trickle"
    OUTPUT_NODE = True

    def report(self) -> dict[str, Any]:
        status = get_login_session().public_status()
        text = str(status.get("message") or status.get("status") or "")
        return {
            "ui": {"text": [text]},
            "result": (
                str(status.get("status") or ""),
                str(status.get("user_code") or ""),
                str(status.get("usage_summary") or ""),
            ),
        }


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

    @routes.post("/livepeer-ai-nodes/pymthouse/login")
    async def start_login(_request):
        try:
            payload = await get_login_session().begin_login()
        except PymtHouseAuthError as exc:
            return web.json_response(_error_payload(exc.message), status=400)
        except Exception:
            LOGGER.exception("PymtHouse login route failed")
            return web.json_response(_error_payload("Sign-in failed."), status=500)
        return web.json_response(payload)

    @routes.get("/livepeer-ai-nodes/pymthouse/status")
    async def login_status(_request):
        return web.json_response(get_login_session().public_status())

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
    }


register_login_routes()

NODE_CLASS_MAPPINGS = {
    "PymtHouseLogin": PymtHouseLogin,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PymtHouseLogin": "PymtHouse Login",
}
