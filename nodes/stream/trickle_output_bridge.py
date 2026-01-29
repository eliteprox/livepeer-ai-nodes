import asyncio
import logging
import threading
import time
from typing import Dict, Optional, Tuple

import numpy as np


LOGGER = logging.getLogger("comfyui_trickle.trickle_output_bridge")


class TrickleOutputBridge:
    """
    Thread-safe buffer that stores the most recent output frame received from the 
    trickle subscriber.

    The subscriber (running inside an asyncio loop) calls `put_frame_sync`
    whenever a new frame arrives. ComfyUI nodes call `get_frame_sync` to
    fetch the most recent image synchronously.
    """

    def __init__(self, frame_width: int = 1280, frame_height: int = 720):
        self._thread_lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_timestamp: float = 0.0
        self._frames_received: int = 0
        self.frame_width = frame_width
        self.frame_height = frame_height
        self._blank_template = self._make_blank(frame_width, frame_height)

    @staticmethod
    def _make_blank(width: int, height: int) -> np.ndarray:
        return np.zeros((height, width, 3), dtype=np.uint8)

    async def put_frame(self, frame: np.ndarray) -> None:
        """Async method called by the subscriber to store a frame."""
        if not isinstance(frame, np.ndarray):
            raise TypeError("frame must be numpy.ndarray")
        if frame.ndim != 3 or frame.shape[2] not in (3, 4):
            raise ValueError("frame must be HxWxC (RGB/RGBA)")

        normalized = frame[:, :, :3]
        if normalized.dtype != np.uint8:
            normalized = np.clip(normalized, 0, 255).astype(np.uint8)

        with self._thread_lock:
            self._latest_frame = normalized.copy()
            self._latest_timestamp = time.time()
            self._frames_received += 1
            self.frame_height, self.frame_width = normalized.shape[:2]
            self._blank_template = self._make_blank(self.frame_width, self.frame_height)
            LOGGER.debug(
                "Trickle bridge stored frame %sx%s (total=%s)",
                self.frame_width,
                self.frame_height,
                self._frames_received,
            )

    def put_frame_sync(self, frame: np.ndarray) -> None:
        """Synchronous method to store a frame (for thread-safe access)."""
        if not isinstance(frame, np.ndarray):
            raise TypeError("frame must be numpy.ndarray")
        if frame.ndim != 3 or frame.shape[2] not in (3, 4):
            raise ValueError("frame must be HxWxC (RGB/RGBA)")

        normalized = frame[:, :, :3]
        if normalized.dtype != np.uint8:
            normalized = np.clip(normalized, 0, 255).astype(np.uint8)

        with self._thread_lock:
            self._latest_frame = normalized.copy()
            self._latest_timestamp = time.time()
            self._frames_received += 1
            self.frame_height, self.frame_width = normalized.shape[:2]
            self._blank_template = self._make_blank(self.frame_width, self.frame_height)

    def get_frame_sync(self) -> Tuple[Optional[np.ndarray], float]:
        """Synchronous method to get the latest frame (for ComfyUI nodes)."""
        with self._thread_lock:
            if self._latest_frame is None:
                return None, 0.0
            return self._latest_frame.copy(), self._latest_timestamp

    def get_frame_or_blank_sync(self) -> Tuple[np.ndarray, float, bool]:
        """Synchronous method to get the latest frame or a blank frame."""
        frame, timestamp = self.get_frame_sync()
        if frame is None:
            return self.blank_frame(), 0.0, False
        return frame, timestamp, True

    def reset_sync(self) -> None:
        """Synchronous reset."""
        with self._thread_lock:
            self._latest_frame = None
            self._latest_timestamp = 0.0
            self._frames_received = 0

    def blank_frame(self) -> np.ndarray:
        return self._blank_template.copy()

    def stats_sync(self) -> Dict[str, float]:
        """Synchronous stats."""
        with self._thread_lock:
            return {
                "frames_received": self._frames_received,
                "timestamp": self._latest_timestamp,
                "frame_width": self.frame_width,
                "frame_height": self.frame_height,
            }

    @property
    def has_frame(self) -> bool:
        with self._thread_lock:
            return self._latest_frame is not None

    @property
    def frames_received(self) -> int:
        with self._thread_lock:
            return self._frames_received


TRICKLE_OUTPUT_BRIDGE = TrickleOutputBridge()
