"""
Shared helpers for resolving Livepeer network configuration.

Simple resolution from node inputs or environment variables.
"""

from __future__ import annotations

import os
from typing import Tuple

DEFAULT_ORCH_URL = "https://hky.eliteencoder.net:8936"
DEFAULT_SIGNER_URL = "http://localhost:8081"
ENV_ORCH_URL = "ORCHESTRATOR_URL"
ENV_SIGNER_URL = "SIGNER_URL"


def resolve_network_config(
    orchestrator_url: str = "",
    signer_url: str = "",
) -> Tuple[str, str]:
    """
    Resolve orchestrator and signer endpoints for the Livepeer network.
    
    Preference order:
    1. Explicit parameters supplied by caller (from TrickleConfig node)
    2. Process environment variables
    3. Default orchestrator URL (signer is optional)
    """

    resolved_orch = (orchestrator_url or os.environ.get(ENV_ORCH_URL, DEFAULT_ORCH_URL)).strip()
    resolved_signer = (signer_url or os.environ.get(ENV_SIGNER_URL, DEFAULT_SIGNER_URL)).strip()

    if not resolved_orch:
        raise ValueError(
            "Orchestrator URL is missing. Set ORCHESTRATOR_URL or pass orchestrator_url."
        )

    return resolved_orch, resolved_signer


__all__ = ["resolve_network_config"]
