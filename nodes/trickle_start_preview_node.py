import asyncio
import hashlib
import logging
import time
from typing import Any, Dict

import numpy as np
import torch

from .frame_nodes import StartTrickleStream, _NETWORK_RUNTIME
from .stream.http_stream_proxy import get_stream_proxy
from .stream.trickle_output_bridge import TRICKLE_OUTPUT_BRIDGE

LOGGER = logging.getLogger("comfyui_trickle.start_preview")


def _ensure_proxy_stream_url(subscribe_url: str, proxy_port: int) -> str:
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


class TrickleStartAndPreview:
    """
    Start a Livepeer trickle stream and immediately expose subscribe_url,
    an MPEG-TS stream URL, a browser viewer URL, and the latest frame.
    """

    INITIAL_FRAME_WAIT_SECONDS = 2.5
    INITIAL_FRAME_POLL_SECONDS = 0.05

    def __init__(self):
        self._starter = StartTrickleStream()

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "config": ("TRICKLE_CONFIG",),
            },
            "optional": {
                "width": ("INT", {"default": 512, "min": 1, "max": 4096}),
                "height": ("INT", {"default": 512, "min": 1, "max": 4096}),
                "start_seq": ("INT", {"default": -2}),
                "proxy_port": ("INT", {"default": 8765, "min": 1024, "max": 65535}),
                "enabled": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "IMAGE")
    RETURN_NAMES = ("publish_url", "subscribe_url", "stream_url", "viewer_url", "image")
    FUNCTION = "start_and_preview"
    CATEGORY = "Livepeer/Trickle"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs) -> float:
        return float("nan")

    def start_and_preview(
        self,
        config: Dict[str, Any],
        width: int = 512,
        height: int = 512,
        start_seq: int = -2,
        proxy_port: int = 8765,
        enabled: bool = True,
    ):
        if not enabled:
            # Stop any running stream and return blank outputs.
            self._starter._stop_stream()
            return {
                "ui": {"text": ["Stream stopped"]},
                "result": ("", "", "", "", self._blank_image()),
            }

        status = self._starter.start_trickle_stream(
            config,
            width,
            height,
            start_seq,
            True,
        )

        # start_trickle_stream returns dict with "ui" on error
        if isinstance(status, dict) and "ui" in status and "result" not in status:
            return {
                "ui": status.get("ui", {}),
                "result": ("", "", "", self._blank_image()),
            }

        status_values = status.get("result", None) if isinstance(status, dict) else status
        if not status_values:
            status_values = ("", "", "", "")

        publish_url, _, subscribe_url, error_msg = status_values

        if error_msg or not subscribe_url:
            msg = error_msg or "No subscribe URL; stream failed to start."
            return {
                "ui": {"text": [f"ERROR: {msg}"]},
                "result": ("", "", "", "", self._blank_image()),
            }

        try:
            stream_url = _ensure_proxy_stream_url(subscribe_url, proxy_port)
            stream_session_id = hashlib.sha1(
                f"{subscribe_url}|gen={StartTrickleStream._stream_generation}".encode("utf-8")
            ).hexdigest()[:12]
            viewer_url = (
                f"/livepeer/viewer?stream={stream_url}"
                f"&sid={stream_session_id}"
            )
            image = self._pull_latest_image()
            return {
                "ui": {"text": [f"Iframe URL: {viewer_url}", f"Stream: {stream_url}"]},
                "result": (publish_url, subscribe_url, stream_url, viewer_url, image),
            }
        except Exception as exc:
            error_msg = f"Failed to start preview: {exc}"
            LOGGER.error("TrickleStartAndPreview error: %s", exc, exc_info=True)
            return {
                "ui": {"text": [f"ERROR: {error_msg}"]},
                "result": ("", "", "", "", self._blank_image()),
            }

    def _pull_latest_image(self) -> torch.Tensor:
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
            LOGGER.error("TrickleStartAndPreview image output error: %s", exc)
            return self._blank_image()

    @staticmethod
    def _blank_image(width: int = 512, height: int = 512) -> torch.Tensor:
        blank = torch.zeros((height, width, 3), dtype=torch.float32)
        return blank.unsqueeze(0)


NODE_CLASS_MAPPINGS = {
    "TrickleStartAndPreview": TrickleStartAndPreview,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "TrickleStartAndPreview": "Trickle Start + Preview",
}
