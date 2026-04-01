"""
Shared helpers for resolving Livepeer network configuration.

Package-level constants for billing/OIDC auth and orchestrator helpers.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

BILLING_URL = "https://pymthouse.com"
CLIENT_ID = "app_3ee9ae159ea7ad4c03b52849"
OIDC_SCOPES = "openid profile gateway"

ENV_ORCH_URL = "ORCHESTRATOR_URL"

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
    Browser popup flow only (PKCE).

    This extension no longer supports device/headless auth flow.
    """
    return False


def cached_auth_status() -> Tuple[bool, str]:
    """
    Check whether a valid cached OIDC token is available for streaming.
    """
    try:
        from livepeer_gateway.oidc_auth import load_cached_token
    except Exception as exc:
        return False, f"Could not import OIDC auth client: {exc}"

    token = load_cached_token(
        BILLING_URL,
        client_id=CLIENT_ID,
        scopes=OIDC_SCOPES,
    )
    if token is None:
        return False, "No saved Livepeer login session found."
    if token.is_expired():
        return False, "Saved Livepeer login session is expired."
    return True, ""


def try_refresh_auth_token() -> Tuple[bool, str]:
    """
    Attempt to refresh the cached OIDC token set once.
    """
    try:
        from livepeer_gateway.oidc_auth import (
            load_cached_token,
            refresh,
            save_cached_token,
        )
    except Exception as exc:
        return False, f"Could not import OIDC refresh client: {exc}"

    cached = load_cached_token(
        BILLING_URL,
        client_id=CLIENT_ID,
        scopes=OIDC_SCOPES,
    )
    if cached is None:
        return False, "No saved Livepeer session found to refresh."

    refresh_token = cached.get("refresh_token")
    if not refresh_token:
        return False, "Saved session has no refresh token."

    try:
        tokens = refresh(
            BILLING_URL,
            refresh_token,
            client_id=CLIENT_ID,
        )
        save_cached_token(
            BILLING_URL,
            tokens,
            client_id=CLIENT_ID,
            scopes=OIDC_SCOPES,
        )
    except Exception as exc:
        return False, f"Refresh request failed: {exc}"

    return True, ""


__all__ = [
    "BILLING_URL",
    "CLIENT_ID",
    "cached_auth_status",
    "get_auth_headless",
    "OIDC_SCOPES",
    "resolve_orchestrator",
    "try_refresh_auth_token",
]
