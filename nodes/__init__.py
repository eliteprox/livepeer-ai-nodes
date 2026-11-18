"""ComfyUI node registration.

Importing this package must stay light. Auth and orchestrator resolution are
imported by tests that cannot load ComfyUI or torch. Node classes are loaded
only when ComfyUI asks for the mapping tables.
"""

from __future__ import annotations

_MAPPINGS: tuple[dict, dict] | None = None


def _load_node_mappings() -> tuple[dict, dict]:
    global _MAPPINGS
    if _MAPPINGS is not None:
        return _MAPPINGS

    import logging
    import sys

    from . import creative_remote, frame_nodes, js, pipeline_config_sdxl, pymthouse_login, remote_sessions
    from . import remote_workflow, session_recorder, stream_url_node

    base_logger = logging.getLogger("comfyui_trickle")
    has_handler = any(getattr(handler, "_comfyui_trickle_handler", False) for handler in base_logger.handlers)
    if not has_handler:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        handler._comfyui_trickle_handler = True
        base_logger.addHandler(handler)
    base_logger.setLevel(logging.INFO)
    base_logger.propagate = True

    class_mappings: dict = {}
    display_mappings: dict = {}
    for module in (
        session_recorder,
        remote_workflow,
        creative_remote,
        remote_sessions,
        frame_nodes,
        stream_url_node,
        pipeline_config_sdxl,
        pymthouse_login,
        js,
    ):
        class_mappings.update(module.NODE_CLASS_MAPPINGS)
        display_mappings.update(module.NODE_DISPLAY_NAME_MAPPINGS)
    _MAPPINGS = (class_mappings, display_mappings)
    return _MAPPINGS


def __getattr__(name: str):
    if name in {"NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"}:
        class_mappings, display_mappings = _load_node_mappings()
        return class_mappings if name == "NODE_CLASS_MAPPINGS" else display_mappings
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
