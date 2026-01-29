from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
import time
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import av
import numpy as np

# Enable HTTP signers for local development (must be set before importing SDK)
os.environ.setdefault("ALLOW_HTTP_SIGNER", "1")


from livepeer_gateway.media_publish import MediaPublish, MediaPublishConfig
from livepeer_gateway.orchestrator import (
    GetOrchestratorInfo,
    LiveVideoToVideo,
    StartJob,
    StartJobRequest,
)


from .frame_bridge import FRAME_BRIDGE, array_to_av_frame, normalize_uint8_frame

LOGGER = logging.getLogger("comfyui_trickle.network_controller")


def _ensure_allow_http_signer() -> None:
    """
    Ensure ALLOW_HTTP_SIGNER is set before initializing orchestrator client.
    """
    if os.environ.get("ALLOW_HTTP_SIGNER") != "1":
        os.environ["ALLOW_HTTP_SIGNER"] = "1"
        LOGGER.debug("ALLOW_HTTP_SIGNER set to 1 for orchestrator client initialization")


# Enforce orchestrator URLs stay https (transcoder endpoints are always https)
def _require_https_orchestrator(url: str) -> str:
    url = (url or "").strip()
    parsed = urlparse(url if "://" in url else f"https://{url}")
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"Orchestrator URL must be https://host:port (got {url!r})")
    if parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
        raise ValueError(f"Orchestrator URL must not include a path/query/fragment: {url!r}")
    return f"https://{parsed.netloc}"


@dataclass
class NetworkControllerConfig:
    orchestrator_url: str
    signer_url: Optional[str] = None
    model_id: str = "comfystream"
    fps: float = 30.0
    frame_width: int = 512
    frame_height: int = 512
    keyframe_interval_s: float = 2.0


class NetworkController:
    """
    Trickle-based media publisher that streams frames directly to an orchestrator.
    Runs a dedicated asyncio loop in a background thread so ComfyUI nodes can
    enqueue frames synchronously.
    """

    def __init__(self, config: NetworkControllerConfig):
        self.config = config
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self.job: Optional[LiveVideoToVideo] = None
        self.media: Optional[MediaPublish] = None
        self._publisher_task: Optional[asyncio.Task] = None
        self._events_task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self.frames_sent: int = 0
        self.frames_repeated: int = 0
        self.running: bool = False
        self._stream_state: "NetworkController.StreamState" = self.StreamState.IDLE
        self._last_error: Optional[str] = None
        self._started_at: Optional[float] = None
        self._events_wait_seconds: float = 8.0

    class StreamState(Enum):
        IDLE = "idle"
        STARTING = "starting"
        RUNNING = "running"
        DEGRADED = "degraded"
        ERROR = "error"
        CLOSED = "closed"

    def update_config(self, config: NetworkControllerConfig) -> None:
        self.config = config

    def _ensure_loop(self) -> None:
        if self.loop:
            return
        loop = asyncio.new_event_loop()
        self.loop = loop
        self._thread = threading.Thread(target=loop.run_forever, name="network-controller-loop", daemon=True)
        self._thread.start()
        FRAME_BRIDGE.attach_loop(loop)
        LOGGER.info("NetworkController event loop started")

    def start(
        self,
        *,
        model_id: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._ensure_loop()
        if not self.loop:
            raise RuntimeError("Failed to initialize asyncio loop")

        fut = asyncio.run_coroutine_threadsafe(
            self._start_async(
                model_id=model_id or self.config.model_id,
                params=params or {},
                request_id=request_id,
            ),
            self.loop,
        )
        return fut.result(timeout=30)

    def stop(self) -> Dict[str, Any]:
        if not self.loop:
            return {"running": False}
        fut = asyncio.run_coroutine_threadsafe(self._stop_async(), self.loop)
        try:
            fut.result(timeout=15)
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.warning("Stop failed: %s", exc)
        return self.status()

    def status(self) -> Dict[str, Any]:
        info = self.job
        return {
            "running": self.running,
            "frames_sent": self.frames_sent,
            "frames_repeated": self.frames_repeated,
            "publish_url": info.publish_url if info else "",
            "subscribe_url": info.subscribe_url if info else "",
            "control_url": info.control_url if info else "",
            "manifest_id": info.manifest_id if info else "",
            "model_id": self.config.model_id,
            "fps": self.config.fps,
            "frame_width": self.config.frame_width,
            "frame_height": self.config.frame_height,
            "state": self._stream_state.value,
            "last_error": self._last_error or "",
            "started_at": self._started_at or 0.0,
        }

    async def _start_async(
        self,
        *,
        model_id: str,
        params: Dict[str, Any],
        request_id: Optional[str],
    ) -> Dict[str, Any]:
        _ensure_allow_http_signer()
        await self._stop_async()
        assert self.loop
        self._stream_state = self.StreamState.STARTING
        self._last_error = None
        self.frames_repeated = 0
        self._started_at = time.perf_counter()

        orch_url = _require_https_orchestrator(self.config.orchestrator_url)
        LOGGER.info("Fetching orchestrator info for %s", self.config.orchestrator_url)
        info = GetOrchestratorInfo(
            orch_url,
            signer_url=self.config.signer_url,
            model_id=model_id,
        )
        LOGGER.info("Starting job model_id=%s", model_id)
        job = StartJob(
            info,
            StartJobRequest(
                model_id=model_id,
                params=params or None,
                request_id=request_id,
            ),
            signer_base_url=self.config.signer_url,
        )
        media = job.start_media(
            MediaPublishConfig(
                fps=self.config.fps,
                keyframe_interval_s=self.config.keyframe_interval_s,
            )
        )

        self.job = job
        self.media = media
        self.frames_sent = 0
        self.running = True
        self._stop_event = asyncio.Event()
        self._publisher_task = asyncio.create_task(self._publisher_loop(), name="network-publisher")
        if self.job and self.job.events:
            self._events_task = asyncio.create_task(self._events_monitor_loop(), name="network-events")
        LOGGER.info("NetworkController started publish_url=%s", job.publish_url)
        return self.status()

    async def _stop_async(self) -> None:
        self.running = False
        if self._stop_event:
            self._stop_event.set()
        if self._publisher_task:
            self._publisher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._publisher_task
            self._publisher_task = None
        if self._events_task:
            self._events_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._events_task
            self._events_task = None
        if self.media:
            with contextlib.suppress(Exception):
                await self.media.close()
        if self.job:
            with contextlib.suppress(Exception):
                await self.job.close()
        self.job = None
        self.media = None
        self._publisher_task = None
        self._stop_event = None
        self.frames_sent = 0
        self.frames_repeated = 0
        self._stream_state = self.StreamState.CLOSED
        self._last_error = None
        self._started_at = None

    async def _publisher_loop(self) -> None:
        assert self.media
        pts = 0
        time_base = Fraction(1, int(round(self.config.fps)))
        frame_interval = 1.0 / float(self.config.fps) if self.config.fps > 0 else 0.033
        next_frame_time = time.perf_counter()
        last_frame: Optional[np.ndarray] = None

        while self.running and self._stop_event and not self._stop_event.is_set():
            now = time.perf_counter()
            delay = max(0.0, next_frame_time - now)

            got_new_frame = False

            # If we don't have a frame yet, wait indefinitely for the first one
            # (up to a reasonable timeout to allow graceful shutdown checks)
            if last_frame is None:
                try:
                    frame = await asyncio.wait_for(FRAME_BRIDGE.queue.get(), timeout=1.0)
                    last_frame = frame
                    got_new_frame = True
                    next_frame_time = time.perf_counter()  # Reset timing from first frame
                except asyncio.TimeoutError:
                    continue  # Keep waiting for first frame
            else:
                # We have a frame to repeat - use FPS-based timing
                try:
                    frame = await asyncio.wait_for(FRAME_BRIDGE.queue.get(), timeout=max(0.001, delay))
                    last_frame = frame
                    got_new_frame = True
                except asyncio.TimeoutError:
                    frame = last_frame
                    self.frames_repeated += 1

            if frame is None:
                continue

            try:
                av_frame = self._to_av_frame(frame, pts, time_base)
                await self.media.write_frame(av_frame)
                self.frames_sent += 1
                pts += 1
                if self._stream_state == self.StreamState.STARTING:
                    self._stream_state = self.StreamState.RUNNING
                if got_new_frame:
                    self._stream_state = self.StreamState.RUNNING
                else:
                    self._stream_state = self.StreamState.DEGRADED
            except Exception as exc:  # pragma: no cover - network/encode errors
                exc_str = str(exc).lower()
                # Detect fatal errors (404 stream not found, POST failed, encoder failed, etc.)
                is_fatal = (
                    "404" in exc_str or
                    "status=404" in exc_str or
                    "not found" in exc_str or
                    "stream not found" in exc_str or
                    "post failed" in exc_str or
                    "encoder failed" in exc_str or
                    "mediapublish" in exc_str
                )
                if is_fatal:
                    LOGGER.error("Stream terminated (fatal error): %s", exc)
                    self._stream_state = self.StreamState.ERROR
                    self._last_error = str(exc)
                    self.running = False
                    if self._stop_event:
                        self._stop_event.set()
                    break
                LOGGER.warning("Failed to publish frame (will retry): %s", exc)

            next_frame_time += frame_interval
            # Prevent drift on large delays
            if next_frame_time < time.perf_counter() - frame_interval:
                next_frame_time = time.perf_counter() + frame_interval

        # Mark as not running when loop exits
        if self._stream_state == self.StreamState.ERROR:
            self.running = False
        LOGGER.info("Publisher loop exit (state=%s, running=%s)", self._stream_state.value, self.running)

    async def _events_monitor_loop(self) -> None:
        """
        Monitor events URL for stream status and errors. Uses SDK's job.events().
        """
        assert self.job
        start_wait = time.perf_counter()
        attempt = 0
        while True:
            try:
                attempt += 1
                async for event in self.job.events(max_buffered_events=64, overflow="drop_oldest"):
                    event_type = event.get("event_type")
                    if event_type == "status":
                        inference_status = event.get("inference_status")
                        LOGGER.info("Stream status event: %s", inference_status)
                # If the iterator exits gracefully, treat as closed
                break
            except Exception as exc:
                elapsed = time.perf_counter() - start_wait
                if elapsed <= self._events_wait_seconds:
                    LOGGER.info(
                        "Events stream not ready (attempt=%s, elapsed=%.2fs): %s",
                        attempt,
                        elapsed,
                        exc,
                    )
                    await asyncio.sleep(0.5)
                    continue
                LOGGER.error("Events stream error after retries: %s", exc)
                self._stream_state = self.StreamState.ERROR
                self._last_error = str(exc)
                if self._stop_event:
                    self._stop_event.set()
                break

        if self._stream_state not in (self.StreamState.ERROR, self.StreamState.CLOSED):
            self._stream_state = self.StreamState.CLOSED
            self._last_error = "Events stream closed"
            LOGGER.info("Events stream closed, marking stream as CLOSED")
        
        # Signal that stream has ended
        self.running = False
        if self._stop_event:
            self._stop_event.set()

    def get_health(self) -> Dict[str, Any]:
        """
        Expose current stream health for UI consumption.
        """
        return {
            "state": self._stream_state.value,
            "running": self.running,
            "frames_sent": self.frames_sent,
            "frames_repeated": self.frames_repeated,
            "last_error": self._last_error or "",
            "queue_depth": FRAME_BRIDGE.depth(),
        }

    def is_healthy(self) -> bool:
        """
        Stream is healthy when running, gracefully degraded (repeating frames), or
        starting within the events wait window.
        """
        if self._stream_state in (self.StreamState.RUNNING, self.StreamState.DEGRADED):
            return True
        if self._stream_state == self.StreamState.STARTING and self._started_at is not None:
            return (time.perf_counter() - self._started_at) <= self._events_wait_seconds
        return False

    def check_tasks_alive(self) -> bool:
        """
        Check if background tasks (publisher, events) are still running.
        This is a quick non-blocking check to detect if the stream has died.
        Returns True if tasks are alive, False if any task has exited.
        """
        if not self.running:
            return False
        
        # Check if publisher task has finished (exited = stream dead)
        if self._publisher_task and self._publisher_task.done():
            exc = self._publisher_task.exception() if not self._publisher_task.cancelled() else None
            if exc:
                LOGGER.warning("Publisher task exited with exception: %s", exc)
                self._last_error = str(exc)
            else:
                LOGGER.warning("Publisher task exited")
            self._stream_state = self.StreamState.CLOSED
            self.running = False
            return False
        
        # Check if events task has finished (events stream closed = stream likely dead)
        if self._events_task and self._events_task.done():
            exc = self._events_task.exception() if not self._events_task.cancelled() else None
            if exc:
                LOGGER.warning("Events task exited with exception: %s", exc)
                if not self._last_error:
                    self._last_error = str(exc)
            else:
                LOGGER.debug("Events task exited (stream closed)")
            # Events closing doesn't immediately mean error, but indicates stream ended
            if self._stream_state not in (self.StreamState.ERROR,):
                self._stream_state = self.StreamState.CLOSED
            self.running = False
            return False
        
        return True

    def _to_av_frame(self, frame: np.ndarray, pts: int, time_base: Fraction) -> av.VideoFrame:
        rgb = normalize_uint8_frame(frame)
        return array_to_av_frame(
            rgb,
            pts=pts,
            fps=self.config.fps,
            width=self.config.frame_width,
            height=self.config.frame_height,
        )
