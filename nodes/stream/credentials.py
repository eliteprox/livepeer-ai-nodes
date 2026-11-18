"""
Shared helpers for resolving Livepeer network configuration.

Orchestrator selection is a pure function of the node field, the process
environment, and a discovery inventory. Nothing in this module opens a socket.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Tuple
from urllib.parse import urlparse

DEFAULT_SIGNER_URL = "http://localhost:8081"
ENV_ORCH_URL = "ORCHESTRATOR_URL"
ENV_SIGNER_URL = "SIGNER_URL"
LOCALHOST_ORCHESTRATOR_PLACEHOLDER = "https://localhost:8935"
NO_ORCHESTRATOR_MESSAGE = "No orchestrator available right now, try again"
_INVENTORY_KEYS = ("orchestrators", "data", "entries")


def is_explicit_orchestrator(url: str) -> bool:
    """An empty field and the old localhost placeholder are not overrides."""
    text = (url or "").strip().rstrip("/")
    return bool(text) and text != LOCALHOST_ORCHESTRATOR_PLACEHOLDER


def select_discovered_orchestrator(inventory: object) -> str:
    """Pick an HTTPS orchestrator from a discovery inventory.

    Prefer the first address that has a runner with ``capacity_available > 0``.
    Otherwise use the first HTTPS address. HTTP addresses are ignored.
    """
    chosen = ""
    for address, has_capacity in _https_orchestrators(inventory):
        if has_capacity:
            return address
        if not chosen:
            chosen = address
    if chosen:
        return chosen
    raise ValueError(NO_ORCHESTRATOR_MESSAGE)


def resolve_network_config(
    orchestrator_url: str = "",
    signer_url: str = "",
    *,
    env: Mapping[str, str] | None = None,
    discovery: object | None = None,
    signed_in: bool = False,
    discovery_loader: Callable[[], object] | None = None,
) -> Tuple[str, str]:
    """Resolve orchestrator and signer endpoints.

    Orchestrator preference:
    1. Explicit node field, except an empty value or ``https://localhost:8935``
    2. ``ORCHESTRATOR_URL`` when that variable is non-blank
    3. Discovery inventory when the caller is signed in

    The signer remains optional and still falls back to the local signer.
    """
    source = os.environ if env is None else env
    resolved_orch = resolve_orchestrator_url(
        orchestrator_url,
        env=source,
        discovery=discovery,
        signed_in=signed_in,
        discovery_loader=discovery_loader,
    )
    resolved_signer = _signer_url(signer_url, source)
    return resolved_orch, resolved_signer


def resolve_orchestrator_url(
    orchestrator_url: str = "",
    *,
    env: Mapping[str, str] | None = None,
    discovery: object | None = None,
    signed_in: bool = False,
    discovery_loader: Callable[[], object] | None = None,
) -> str:
    if is_explicit_orchestrator(orchestrator_url):
        return orchestrator_url.strip()
    source = os.environ if env is None else env
    env_url = _text(source.get(ENV_ORCH_URL))
    if env_url:
        return env_url
    if not signed_in:
        raise ValueError(
            "Orchestrator URL is missing. Set ORCHESTRATOR_URL or pass orchestrator_url."
        )
    payload = discovery
    if payload is None and discovery_loader is not None:
        payload = discovery_loader()
    return select_discovered_orchestrator(payload)


def _signer_url(signer_url: str, env: Mapping[str, str]) -> str:
    explicit = _text(signer_url)
    if explicit:
        return explicit
    if ENV_SIGNER_URL in env:
        return _text(env.get(ENV_SIGNER_URL))
    return DEFAULT_SIGNER_URL


def _https_orchestrators(inventory: object):
    for item in _inventory_entries(inventory):
        address = _https_address(item.get("address"))
        if address:
            yield address, _has_capacity(item.get("runners"))


def _inventory_entries(inventory: object):
    entries = inventory
    if isinstance(inventory, dict):
        entries = next(
            (inventory[key] for key in _INVENTORY_KEYS if isinstance(inventory.get(key), list)),
            [],
        )
    if not isinstance(entries, list):
        return
    for item in entries:
        if isinstance(item, dict):
            yield item


def _https_address(address: object) -> str:
    if not isinstance(address, str):
        return ""
    text = address.strip()
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    return text


def _has_capacity(runners: object) -> bool:
    if not isinstance(runners, list):
        return False
    for runner in runners:
        if not isinstance(runner, dict):
            continue
        capacity = runner.get("capacity_available")
        if isinstance(capacity, bool) or not isinstance(capacity, (int, float)):
            continue
        if capacity > 0:
            return True
    return False


def _text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


__all__ = [
    "is_explicit_orchestrator",
    "resolve_network_config",
    "resolve_orchestrator_url",
    "select_discovered_orchestrator",
]
