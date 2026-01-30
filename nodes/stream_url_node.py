"""
ComfyUI node that creates a playable MPEG-TS URL from trickle subscribe streams.
The URL can be opened in VLC, MPV, or any video player that supports network streams.
"""
import asyncio
import logging
from typing import Any, Dict

from .stream.http_stream_proxy import get_stream_proxy

LOGGER = logging.getLogger("comfyui_trickle.stream_url")


class TrickleStreamURL:
    """
    Convert a trickle subscribe URL to a standard MPEG-TS stream URL.
    
    This node starts an HTTP proxy server that converts the trickle protocol
    to standard MPEG-TS, outputting a URL that works with:
    - VLC Media Player (recommended)
    - MPV
    - ffplay
    - Any video player supporting HTTP MPEG-TS streams
    
    Usage:
    1. Connect subscribe_url from StartTrickleStream
    2. Copy the output player_url
    3. Open in VLC: Media -> Open Network Stream -> Paste URL
    """

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "optional": {
                "subscribe_url": ("STRING", {
                    "default": "",
                    "tooltip": "Connect from StartTrickleStream to get the subscribe URL",
                }),
                "proxy_port": ("INT", {
                    "default": 8765,
                    "min": 1024,
                    "max": 65535,
                    "tooltip": "HTTP proxy server port",
                }),
                "enabled": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Enable/disable the proxy server",
                }),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("player_url",)
    FUNCTION = "create_stream_url"
    CATEGORY = "Trickle"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs) -> float:
        # Always re-execute to update URL
        return float("nan")

    def create_stream_url(
        self,
        subscribe_url: str = "",
        proxy_port: int = 8765,
        enabled: bool = True,
    ):
        """
        Start the HTTP proxy and return the MPEG-TS stream URL.
        """
        if not enabled:
            return {
                "ui": {"text": ["Proxy disabled"]},
                "result": ("",),
            }

        if not subscribe_url:
            return {
                "ui": {"text": ["No stream URL. Connect subscribe_url from StartTrickleStream."]},
                "result": ("",),
            }

        try:
            # Get or create the proxy server (runs in the network controller's loop)
            from .frame_nodes import _NETWORK_RUNTIME
            
            controller = _NETWORK_RUNTIME.controller
            if not controller or not controller.loop:
                msg = "No active stream controller. Start the stream first."
                return {
                    "ui": {"text": [msg]},
                    "result": ("",),
                }

            # Start proxy in the controller's loop
            future = asyncio.run_coroutine_threadsafe(
                get_stream_proxy(host="127.0.0.1", port=proxy_port),
                controller.loop,
            )
            proxy = future.result(timeout=5.0)

            # Update proxy with current subscribe URL
            proxy.set_subscribe_url(subscribe_url)

            stream_url = proxy.stream_url

            LOGGER.info("Trickle stream URL ready: %s", stream_url)
            
            # Return URL with usage instructions
            message = f"✓ Stream URL ready!\n\nOpen in VLC:\n1. Media -> Open Network Stream\n2. Paste: {stream_url}\n\nOr try: vlc {stream_url}"

            return {
                "ui": {"text": [message]},
                "result": (stream_url,),
            }

        except Exception as exc:
            error_msg = f"Failed to start proxy: {exc}"
            LOGGER.error("TrickleStreamURL error: %s", exc, exc_info=True)
            return {
                "ui": {"text": [f"ERROR: {error_msg}"]},
                "result": ("",),
            }


NODE_CLASS_MAPPINGS = {
    "TrickleStreamURL": TrickleStreamURL,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "TrickleStreamURL": "Trickle Stream URL (for VLC)",
}
