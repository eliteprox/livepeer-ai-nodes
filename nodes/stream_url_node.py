"""
ComfyUI node that creates a playable MPEG-TS URL from trickle subscribe streams.
The URL can be opened in VLC, MPV, or any video player that supports network streams.
"""
import asyncio
import hashlib
import logging
import time
from urllib.parse import quote
from typing import Any, Dict

import numpy as np
import torch

from .stream.trickle_output_bridge import TRICKLE_OUTPUT_BRIDGE
from .stream.http_stream_proxy import get_stream_proxy

LOGGER = logging.getLogger("comfyui_trickle.stream_url")


def _ensure_proxy_stream_url(subscribe_url: str, proxy_port: int) -> str:
    from .frame_nodes import _NETWORK_RUNTIME

    controller = _NETWORK_RUNTIME.controller
    if not controller or not controller.loop:
        raise RuntimeError("No active stream controller. Start the stream first.")

    future = asyncio.run_coroutine_threadsafe(
        get_stream_proxy(host="127.0.0.1", port=proxy_port),
        controller.loop,
    )
    proxy = future.result(timeout=5.0)
    proxy.set_subscribe_url(subscribe_url)
    return proxy.stream_url


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
    RETURN_NAMES = ("stream_url",)
    FUNCTION = "create_stream_url"
    CATEGORY = "Livepeer/Trickle"
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
            stream_url = _ensure_proxy_stream_url(subscribe_url, proxy_port)
            LOGGER.info("Trickle stream URL ready: %s", stream_url)
            return {
                "ui": {"text": [stream_url]},
                "result": (stream_url,),
            }

        except Exception as exc:
            error_msg = f"Failed to start proxy: {exc}"
            LOGGER.error("TrickleStreamURL error: %s", exc, exc_info=True)
            return {
                "ui": {"text": [f"ERROR: {error_msg}"]},
                "result": ("",),
            }


class TrickleBrowserPlayer:
    INITIAL_FRAME_WAIT_SECONDS = 2.5
    INITIAL_FRAME_POLL_SECONDS = 0.05

    """
    Open a browser viewer that plays the trickle output continuously.

    Reuses the MPEG-TS proxy from TrickleStreamURL and exposes a viewer URL
    served by the ComfyUI backend for low-friction monitoring during runs.
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
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "IMAGE")
    RETURN_NAMES = ("stream_url", "viewer_url", "image")
    FUNCTION = "open_player"
    CATEGORY = "Livepeer/Trickle"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs) -> float:
        return float("nan")

    def open_player(
        self,
        subscribe_url: str = "",
        proxy_port: int = 8765,
    ):
        if not subscribe_url:
            msg = "No stream URL. Connect subscribe_url from StartTrickleStream."
            return {
                "ui": {"text": [msg]},
                "result": ("", "", self._blank_image()),
            }

        try:
            stream_url = _ensure_proxy_stream_url(subscribe_url, proxy_port)
            stream_session_id = hashlib.sha1(subscribe_url.encode("utf-8")).hexdigest()[:12]
            viewer_url = (
                f"/livepeer/viewer?stream={quote(stream_url, safe='')}"
                f"&sid={stream_session_id}"
            )
            image = self._pull_latest_image()

            return {
                "ui": {"text": [f"Iframe URL: {viewer_url}", f"Stream: {stream_url}"]},
                "result": (stream_url, viewer_url, image),
            }
        except Exception as exc:
            error_msg = f"Failed to open browser player: {exc}"
            LOGGER.error("TrickleBrowserPlayer error: %s", exc, exc_info=True)
            return {
                "ui": {"text": [f"ERROR: {error_msg}"]},
                "result": ("", "", self._blank_image()),
            }

    def _pull_latest_image(self) -> torch.Tensor:
        from .frame_nodes import _NETWORK_RUNTIME

        subscriber = _NETWORK_RUNTIME.subscriber
        if not subscriber:
            return self._blank_image()
        if not subscriber.running and not subscriber.task_alive:
            return self._blank_image()

        try:
            frame_np, _, has_frame = TRICKLE_OUTPUT_BRIDGE.get_frame_or_blank_sync()
            if not has_frame and subscriber.running and subscriber.task_alive:
                deadline = time.perf_counter() + self.INITIAL_FRAME_WAIT_SECONDS
                while time.perf_counter() < deadline:
                    frame_np, _, has_frame = TRICKLE_OUTPUT_BRIDGE.get_frame_or_blank_sync()
                    if has_frame:
                        break
                    if not subscriber.running or not subscriber.task_alive:
                        break
                    time.sleep(self.INITIAL_FRAME_POLL_SECONDS)
            return torch.from_numpy(frame_np.astype(np.float32) / 255.0).unsqueeze(0)
        except Exception as exc:
            LOGGER.error("TrickleBrowserPlayer image output error: %s", exc)
            return self._blank_image()

    @staticmethod
    def _blank_image(width: int = 512, height: int = 512) -> torch.Tensor:
        blank = torch.zeros((height, width, 3), dtype=torch.float32)
        return blank.unsqueeze(0)


NODE_CLASS_MAPPINGS = {
    "TrickleStreamURL": TrickleStreamURL,
    "TrickleBrowserPlayer": TrickleBrowserPlayer,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "TrickleStreamURL": "Trickle Stream URL (for VLC)",
    "TrickleBrowserPlayer": "Trickle Browser Player",
}
