import asyncio
import logging
from asyncio import QueueEmpty
from collections import deque
from pathlib import Path
from typing import Deque, Dict, List, Optional, Union

import numpy as np
import torch
from PIL import Image
from fractions import Fraction
import av


LOGGER = logging.getLogger("comfyui_trickle.frame_bridge")


class FrameBridge:
    """
    Shared queue that allows ComfyUI custom nodes (sync context) to enqueue frames
    for the async streaming controller.
    """

    def __init__(self, max_size: int = 90):
        self.queue: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=max_size)
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.max_size = max_size
        self._buffer: Deque[np.ndarray] = deque()
        self._dropped_before_loop = 0

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        # If attaching to a new/different loop, recreate the queue
        # This prevents "Queue bound to different event loop" errors
        if self.loop is not loop:
            # Drain any items from the old queue into the buffer first
            if self.loop is not None:
                try:
                    while True:
                        frame = self.queue.get_nowait()
                        self._buffer.append(frame)
                except QueueEmpty:
                    pass
                LOGGER.info(
                    "FrameBridge detaching from old loop, saved %d frames to buffer",
                    len(self._buffer),
                )
            
            # Create a new queue for the new loop
            self.queue = asyncio.Queue(maxsize=self.max_size)
            LOGGER.info("FrameBridge created new queue for loop %s", loop)
        
        self.loop = loop
        LOGGER.info("FrameBridge attached to loop %s", loop)
        if self._buffer:
            LOGGER.info(
                "Flushing %s buffered frames into async queue",
                len(self._buffer),
            )
        self._flush_buffer()

    def _flush_buffer(self) -> None:
        if self.loop is None or not self._buffer:
            return
        while self._buffer:
            frame = self._buffer.popleft()
            self._schedule_put(frame)

    def _buffer_frame(self, frame: np.ndarray) -> None:
        if len(self._buffer) >= self.max_size:
            self._buffer.popleft()
            self._dropped_before_loop += 1
            LOGGER.warning(
                "FrameBridge buffer full before loop ready; dropped oldest buffered frame (total_dropped=%s)",
                self._dropped_before_loop,
            )
        self._buffer.append(frame.copy())
        LOGGER.debug(
            "Buffered frame while loop unavailable (buffer=%s)",
            len(self._buffer),
        )

    def _schedule_put(self, frame: np.ndarray) -> None:
        if self.loop is None:
            self._buffer_frame(frame)
            return
        asyncio.run_coroutine_threadsafe(self.queue.put(frame), self.loop)

    def enqueue(self, frame: np.ndarray) -> None:
        if not isinstance(frame, np.ndarray):
            raise TypeError("frame must be a numpy array")
        if frame.ndim != 3 or frame.shape[2] not in (3, 4):
            raise ValueError("frame must be HxWxC RGB/RGBA")
        if frame.dtype != np.uint8:
            frame = np.clip(frame, 0, 255).astype(np.uint8)
        self._schedule_put(frame[:, :, :3])
        print(f"FrameBridge queued frame {frame.shape[1]}x{frame.shape[0]} (depth={self.depth()})")

    def try_get_nowait(self) -> Optional[np.ndarray]:
        try:
            return self.queue.get_nowait()
        except QueueEmpty:
            return None

    def depth(self) -> int:
        return self.queue.qsize() + len(self._buffer)

    def stats(self) -> Dict[str, int]:
        return {
            "queued": self.queue.qsize(),
            "buffered": len(self._buffer),
            "depth": self.depth(),
            "dropped_before_loop": self._dropped_before_loop,
        }

    def reset(self) -> None:
        """
        Reset the bridge state, clearing the queue and loop reference.
        Call this when stopping a stream to ensure clean state for next stream.
        """
        # Drain queue into buffer for potential reuse
        try:
            while True:
                frame = self.queue.get_nowait()
                self._buffer.append(frame)
        except QueueEmpty:
            pass
        
        # Clear the loop reference so next attach_loop creates fresh queue
        self.loop = None
        # Create a fresh queue (not bound to any loop)
        self.queue = asyncio.Queue(maxsize=self.max_size)
        LOGGER.info("FrameBridge reset (buffered=%d frames)", len(self._buffer))


FRAME_BRIDGE = FrameBridge()


def has_loop() -> bool:
    return FRAME_BRIDGE.loop is not None


def enqueue_array_frame(frame: np.ndarray) -> None:
    FRAME_BRIDGE.enqueue(frame)


def tensor_to_uint8_frame(tensor: torch.Tensor) -> np.ndarray:
    """
    Normalize a ComfyUI tensor (B,H,W,C) or (B,C,H,W) into an RGB uint8 numpy array.
    """

    if tensor is None:
        raise ValueError("tensor is None")
    if not isinstance(tensor, torch.Tensor):
        raise TypeError("tensor must be a torch.Tensor")

    data = tensor
    if data.dim() == 4:
        data = data[0]
    if data.dim() == 3 and data.shape[0] in (1, 3, 4):
        # assume CHW/CAHW and convert to HWC
        data = data.permute(1, 2, 0)

    np_frame = data.detach().cpu().numpy()
    if np_frame.ndim != 3:
        raise ValueError("tensor must resolve to an HxWxC array")

    channels = np_frame.shape[2]
    if channels == 1:
        np_frame = np.repeat(np_frame, 3, axis=2)
    elif channels > 3:
        np_frame = np_frame[:, :, :3]

    if np_frame.dtype.kind == "f" and np_frame.max() <= 1.0:
        np_frame = np_frame * 255.0

    np_frame = np.clip(np_frame, 0, 255).astype(np.uint8)
    return np_frame


def enqueue_tensor_frame(tensor: torch.Tensor) -> None:
    """
    Convert a ComfyUI tensor (B,H,W,C) in float range 0-1 into uint8 array.
    """

    enqueue_array_frame(tensor_to_uint8_frame(tensor))


def normalize_uint8_frame(frame: np.ndarray) -> np.ndarray:
    """
    Ensure an ndarray is RGB uint8.
    """
    if frame.ndim != 3 or frame.shape[2] not in (3, 4):
        raise ValueError("frame must be HxWxC RGB/RGBA")
    normalized = frame[:, :, :3]
    if normalized.dtype != np.uint8:
        normalized = np.clip(normalized, 0, 255).astype(np.uint8)
    return normalized


def array_to_av_frame(frame: np.ndarray, *, pts: int, fps: float, width: int, height: int) -> av.VideoFrame:
    """
    Convert a numpy RGB frame into a yuv420p av.VideoFrame with PTS/time_base set.
    """
    rgb = normalize_uint8_frame(frame)
    video_frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
    video_frame = video_frame.reformat(width=width, height=height, format="yuv420p")
    video_frame.pts = pts
    video_frame.time_base = Fraction(1, int(round(fps)))
    return video_frame


def enqueue_file_frame(image_path: Union[str, Path]) -> None:
    """
    Load an image from disk and enqueue it into the frame bridge.
    Useful when workflows save frames to disk before ingestion.
    """

    if not image_path:
        raise ValueError("image_path is required")
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {path}")
    image = Image.open(path).convert("RGB")
    enqueue_array_frame(np.array(image))


def queue_depth() -> int:
    return FRAME_BRIDGE.depth()


def queue_stats() -> Dict[str, int]:
    return FRAME_BRIDGE.stats()


class FolderFrameSource:
    IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
    OUTPUT_DIR = Path(__file__).resolve().parents[4] / "output"

    def __init__(self) -> None:
        self.files: List[Path] = []
        self.index = 0
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def _refresh_files(self) -> None:
        files: List[Path] = []
        for ext in self.IMAGE_EXTENSIONS:
            files.extend(self.OUTPUT_DIR.glob(f"*{ext}"))
        files.sort(key=lambda p: p.stat().st_mtime)
        self.files = files
        self.index = 0

    def _next_file(self) -> Optional[Path]:
        if not self.files or self.index >= len(self.files):
            self._refresh_files()
        if not self.files:
            return None
        path = self.files[self.index % len(self.files)]
        self.index = (self.index + 1) % len(self.files)
        return path

    def next_frame(self) -> Optional[np.ndarray]:
        path = self._next_file()
        if not path:
            return None
        try:
            image = Image.open(path).convert("RGB")
            return np.array(image)
        except Exception as exc:  # pragma: no cover - IO heavy
            LOGGER.warning("Failed to load folder frame %s: %s", path, exc)
            return None
