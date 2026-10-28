from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from typing import Any, Dict, Optional
from urllib.parse import urlparse
import cv2

import av
import numpy as np

import aiohttp
from livepeer_gateway.live_runner import LiveRunnerSession, stop_runner_session
from livepeer_gateway.selection import reserve_session

from .frame_bridge import FRAME_BRIDGE, array_to_av_frame, normalize_uint8_frame
from .trickle_output_bridge import TRICKLE_OUTPUT_BRIDGE

LOGGER = logging.getLogger("comfyui_trickle.network_controller")


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
    signer_headers: Optional[Dict[str, str]] = None
    model_id: str = "comfystream"
    fps: float = 30.0
    frame_width: int = 512
    frame_height: int = 512
    keyframe_interval_s: float = 2.0
    payment_interval_s: float = 5.0


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
        self.job: Optional[LiveRunnerSession] = None
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._ws_client: Optional[aiohttp.ClientSession] = None
        self._publisher_task: Optional[asyncio.Task] = None
        self._receiver_task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self.frames_sent: int = 0
        self.frames_repeated: int = 0
        self.running: bool = False
        self._stream_state: "NetworkController.StreamState" = self.StreamState.IDLE
        self._last_error: Optional[str] = None
        self._started_at: Optional[float] = None
        self._events_wait_seconds: float = 8.0
        self._file_feed_thread: Optional[threading.Thread] = None
        self._file_feed_stop: Optional[threading.Event] = None
        self._file_feed_path: Optional[str] = None

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
        self.stop_file_feed()
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
            # A ComfyStream live runner accepts input on ws_stream and pushes
            # output frames back on the same socket.  Keep the established
            # node output names so older workflows continue to execute.
            "publish_url": self._ws_url(info.app_url) if info else "",
            "subscribe_url": "",
            "control_url": info.control_url if info else "",
            "manifest_id": info.session_id if info else "",
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
        await self._stop_async()
        assert self.loop
        self._stream_state = self.StreamState.STARTING
        self._last_error = None
        self.frames_repeated = 0
        self._started_at = time.perf_counter()

        orch_url = _require_https_orchestrator(self.config.orchestrator_url)

        if model_id.strip() != "comfystream":
            raise ValueError("This transport supports app=comfystream with an API-format workflow. "
                             "The requested pipeline requires a separate transport: " + model_id)
        if not params:
            params = {
                "1": {"class_type": "LoadTensor", "inputs": {}},
                "2": {"class_type": "SaveTensor", "inputs": {"images": ["1", 0]}},
            }
        # The gateway's current live-runner contract reserves the app first,
        # then exchanges JPEG frames on /ws_stream.  ``model_id`` is retained
        # as a node-facing pipeline label; all current ComfyPeer pipelines run
        # behind the ``comfystream`` live-runner app.
        if model_id.strip() and model_id.strip() != "comfystream":
            LOGGER.info("Starting ComfyStream pipeline label=%s", model_id)
        job = await reserve_session(
            orchestrators=[orch_url],
            app="comfystream",
            signer_url=self.config.signer_url,
            signer_headers=self.config.signer_headers,
            timeout=20.0,
        )
        ws_url = self._ws_url(job.app_url)
        self._ws_client = aiohttp.ClientSession()
        try:
            self.ws = await self._ws_client.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=20.0))
            await self.ws.send_json({
                "type": "start",
                # A caller can supply an API-format Comfy workflow directly.
                "prompts": params or {},
                "width": self.config.frame_width,
                "height": self.config.frame_height,
                "request_id": request_id or "",
            })
            await self._await_ready()
        except Exception:
            await self._close_transport()
            await stop_runner_session(job)
            raise

        self.job = job
        self.frames_sent = 0
        self.running = True
        self._stop_event = asyncio.Event()
        self._publisher_task = asyncio.create_task(self._publisher_loop(), name="network-publisher")
        self._receiver_task = asyncio.create_task(self._receiver_loop(), name="network-receiver")
        LOGGER.info("NetworkController started websocket=%s", ws_url)
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
        if self._receiver_task:
            self._receiver_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._receiver_task
            self._receiver_task = None
        if self.job:
            with contextlib.suppress(Exception):
                await stop_runner_session(self.job)
        await self._close_transport()
        self.job = None
        self._publisher_task = None
        self._stop_event = None
        self.frames_sent = 0
        self.frames_repeated = 0
        self._stream_state = self.StreamState.CLOSED
        self._last_error = None
        self._started_at = None

    async def _publisher_loop(self) -> None:
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
                if self.ws is None or self.ws.closed:
                    raise RuntimeError("ComfyStream WebSocket is closed")
                payload = self._encode_jpeg(frame)
                await self.ws.send_bytes(payload)
                self.frames_sent += 1
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
                    "websocket" in exc_str
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

    async def _await_ready(self) -> None:
        """Wait for the app-level ready message before accepting frames."""
        assert self.ws is not None
        while True:
            message = await self.ws.receive(timeout=20.0)
            if message.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(message.data)
                if data.get("type") == "ready":
                    return
                if data.get("type") == "error":
                    raise RuntimeError(data.get("error") or "ComfyStream rejected the workflow")
            elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                raise RuntimeError("ComfyStream closed before it became ready")

    async def _receiver_loop(self) -> None:
        """Convert remote JPEG output into the existing ComfyUI output bridge."""
        assert self.ws is not None
        try:
            async for message in self.ws:
                if message.type == aiohttp.WSMsgType.BINARY:
                    decoded = cv2.imdecode(np.frombuffer(message.data, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if decoded is None:
                        LOGGER.warning("ComfyStream sent an undecodable output frame")
                        continue
                    TRICKLE_OUTPUT_BRIDGE.put_frame_sync(cv2.cvtColor(decoded, cv2.COLOR_BGR2RGB))
                elif message.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(message.data)
                    if data.get("type") == "error":
                        raise RuntimeError(data.get("error") or "ComfyStream stream error")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.error("ComfyStream receiver stopped: %s", exc)
            self._stream_state = self.StreamState.ERROR
            self._last_error = str(exc)
            self.running = False
            if self._stop_event:
                self._stop_event.set()

    async def _close_transport(self) -> None:
        ws, client = self.ws, self._ws_client
        self.ws, self._ws_client = None, None
        if ws is not None and not ws.closed:
            with contextlib.suppress(Exception):
                await ws.send_str("eos")
            with contextlib.suppress(Exception):
                await ws.close()
        if client is not None and not client.closed:
            await client.close()

    @staticmethod
    def _ws_url(app_url: str) -> str:
        parsed = urlparse(app_url.rstrip("/") + "/ws_stream")
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return parsed._replace(scheme=scheme).geturl()

    @staticmethod
    def _encode_jpeg(frame: np.ndarray) -> bytes:
        rgb = normalize_uint8_frame(frame)
        ok, encoded = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise RuntimeError("Could not encode input frame as JPEG")
        return encoded.tobytes()

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
        if self._receiver_task and self._receiver_task.done():
            exc = self._receiver_task.exception() if not self._receiver_task.cancelled() else None
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

    def stop_file_feed(self) -> None:
        """
        Stop any background file feeder thread pushing frames into the bridge.
        """
        if self._file_feed_stop:
            self._file_feed_stop.set()
        if self._file_feed_thread and self._file_feed_thread.is_alive():
            self._file_feed_thread.join(timeout=2.0)
        self._file_feed_thread = None
        self._file_feed_stop = None
        self._file_feed_path = None

    def start_file_feed(
        self,
        video_path: str,
        *,
        loop_video: bool = True,
        fps_override: Optional[float] = None,
    ) -> None:
        """
        Spawn a background thread that reads frames from a video file and
        enqueues them into the FrameBridge for publishing.
        """
        if not video_path:
            raise ValueError("video_path is required")
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video not found: {video_path}")
        if not self.running or not self.is_healthy():
            raise RuntimeError("Stream must be running before feeding video")

        self.stop_file_feed()

        stop_event = threading.Event()
        self._file_feed_stop = stop_event
        self._file_feed_path = video_path

        self._file_feed_thread = threading.Thread(
            target=self._file_feed_loop,
            args=(video_path, stop_event, loop_video, fps_override),
            name="file-feed-loop",
            daemon=True,
        )
        self._file_feed_thread.start()

    def _file_feed_loop(
        self,
        video_path: str,
        stop_event: threading.Event,
        loop_video: bool,
        fps_override: Optional[float],
    ) -> None:
        """
        Read frames from a video file in a background thread and push them into
        the FrameBridge. Uses the configured output FPS for timing to stay in
        sync with the publisher loop.
        """
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            LOGGER.error("File feed failed to open video: %s", video_path)
            return

        # Use the stream's configured FPS for output timing (not source video FPS)
        # This ensures file feed and publisher stay synchronized
        output_fps = self.config.fps if self.config.fps > 0 else 30.0
        frame_interval = 1.0 / output_fps

        # Get source video info for logging
        source_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or self.config.frame_width)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or self.config.frame_height)

        LOGGER.info(
            "Starting file feed video=%s source_fps=%.3f output_fps=%.3f total_frames=%d size=%dx%d loop=%s",
            video_path,
            source_fps,
            output_fps,
            total_frames,
            width,
            height,
            loop_video,
        )

        frame_count = 0
        next_frame_time = time.perf_counter()

        while not stop_event.is_set() and self.running:
            ret, frame = cap.read()
            if not ret:
                if loop_video:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    frame_count = 0
                    LOGGER.info("File feed: looping video from start")
                    continue
                break

            try:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                if rgb.shape[1] != self.config.frame_width or rgb.shape[0] != self.config.frame_height:
                    rgb = cv2.resize(
                        rgb,
                        (self.config.frame_width, self.config.frame_height),
                        interpolation=cv2.INTER_AREA,
                    )
                FRAME_BRIDGE.enqueue(rgb)
                frame_count += 1
            except Exception as exc:
                LOGGER.error("File feed failed to enqueue frame: %s", exc)
                break

            # Pace frame reading to match output FPS
            next_frame_time += frame_interval
            sleep_time = next_frame_time - time.perf_counter()
            if sleep_time > 0:
                if stop_event.wait(sleep_time):
                    break
            else:
                # We're behind - reset timing to prevent burst catching up
                if sleep_time < -frame_interval:
                    next_frame_time = time.perf_counter()

        cap.release()
        LOGGER.info("File feed thread exiting for video=%s (frames_read=%d)", video_path, frame_count)

    def _to_av_frame(self, frame: np.ndarray, pts: int, time_base: Fraction) -> av.VideoFrame:
        rgb = normalize_uint8_frame(frame)
        return array_to_av_frame(
            rgb,
            pts=pts,
            fps=self.config.fps,
            width=self.config.frame_width,
            height=self.config.frame_height,
        )
