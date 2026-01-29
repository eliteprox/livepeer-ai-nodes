"""
Trickle streaming package for ComfyUI custom nodes.

This package exposes helpers for pushing frames from custom nodes into
the streaming controller's shared queue.

Note: frame_bridge imports are deferred to avoid torch import at package load time.
Import directly from nodes.stream.frame_bridge when needed.
"""

__all__ = [
    "frame_bridge",
    "credentials",
    "network_controller",
    "network_subscriber",
    "trickle_output_bridge",
]
