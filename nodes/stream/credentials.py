"""
Shared helpers for resolving Livepeer network configuration.

Package-level constants for billing/OIDC auth and helpers for
orchestrator resolution and reading ComfyUI settings.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

BILLING_URL = "https://pymthouse.com"
CLIENT_ID = "livepeer-sdk"

ENV_ORCH_URL = "ORCHESTRATOR_URL"

_LOG = logging.getLogger("comfyui_trickle.credentials")


def resolve_orchestrator(orchestrator_url: str = "") -> Optional[str]:
    """
    Resolve the orchestrator endpoint.

    Returns None when no orchestrator is configured, which tells the SDK
    to use discovery (via billing gateway or signer).
    """
    resolved = (orchestrator_url or os.environ.get(ENV_ORCH_URL, "")).strip()
    return resolved or None


def get_auth_headless() -> bool:
    """
    Read the auth_mode setting from ComfyUI's user settings.

    Returns True for device/headless flow (default), False for browser PKCE flow.
    """
    try:
        import folder_paths
        user_dir = Path(folder_paths.get_user_directory())
        settings_path = user_dir / "default" / "comfy.settings.json"
        if settings_path.exists():
            data = json.loads(settings_path.read_text("utf-8"))
            mode = data.get("Livepeer.auth_mode", "device")
            return mode != "browser"
    except Exception as exc:
        _LOG.debug("Could not read auth_mode from ComfyUI settings: %s", exc)
    return True


__all__ = [
    "BILLING_URL",
    "CLIENT_ID",
    "get_auth_headless",
    "resolve_orchestrator",
]
