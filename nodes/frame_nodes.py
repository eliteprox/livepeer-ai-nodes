import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

import folder_paths
import numpy as np
import torch
import cv2

from .stream.credentials import resolve_network_config
from .stream.frame_bridge import enqueue_tensor_frame, enqueue_tensor_batch, queue_depth, has_loop, FRAME_BRIDGE
from .stream.network_controller import NetworkController, NetworkControllerConfig
from .stream.network_subscriber import NetworkSubscriber, NetworkSubscriberConfig
from .stream.trickle_output_bridge import TRICKLE_OUTPUT_BRIDGE


LOGGER = logging.getLogger("comfyui_trickle.nodes")


# --- Network (trickle) runtime state ---


@dataclass
class _NetworkRuntime:
    controller: Optional[NetworkController] = None
    subscriber: Optional[NetworkSubscriber] = None
    last_startup_error: Optional[str] = None  # Track startup failures
    last_subscribe_url: Optional[str] = None  # Track URL to detect stream changes


_NETWORK_RUNTIME = _NetworkRuntime()


# --- Capture nodes ---


class WebcamFrameBatcher:
    """
    Takes single frames from WebcamCapture and accumulates them into batches.
    Lightweight implementation to avoid CPU overhead from hashing/serialization.
    """

    _frame_buffer: list[tuple[torch.Tensor, float]] = []  # (tensor, timestamp)
    _max_buffer_size: int = 64  # Prevent infinite accumulation
    _last_batch_time: float = 0.0
    _buffer_timeout: float = 5.0  # Clear buffer if no batch output for 5 seconds

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "image": ("IMAGE",),
                "batch_size": ("INT", {
                    "default": 8,
                    "min": 1,
                    "max": 32,
                    "tooltip": "Number of frames to accumulate before outputting a batch"
                }),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "batch_frames"
    CATEGORY = "Trickle"
    OUTPUT_NODE = False

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # Always re-executes to keep accumulating frames
        return float("nan")

    def batch_frames(
        self,
        image: torch.Tensor,
        batch_size: int = 8,
    ):
        """
        Accumulate frames from WebcamCapture and output batches when ready.
        Minimal work per frame to keep CPU overhead low.
        """
        current_time = time.perf_counter()
        
        # Auto-reset buffer if it's been too long since last batch (ensures fresh frames)
        if WebcamFrameBatcher._last_batch_time > 0 and (current_time - WebcamFrameBatcher._last_batch_time) > WebcamFrameBatcher._buffer_timeout:
            LOGGER.info(
                "WebcamFrameBatcher: Buffer timeout (%.1fs), clearing stale frames for temporal consistency",
                current_time - WebcamFrameBatcher._last_batch_time
            )
            WebcamFrameBatcher._frame_buffer = []
            WebcamFrameBatcher._last_frame_hash = None
            WebcamFrameBatcher._last_batch_time = current_time
        
        # Add incoming frame to buffer with timestamp (no hashing to avoid CPU cost)
        WebcamFrameBatcher._frame_buffer.append((image, current_time))
        
        # Keep only the most recent frames to avoid reusing stale frames
        if len(WebcamFrameBatcher._frame_buffer) > WebcamFrameBatcher._max_buffer_size:
            WebcamFrameBatcher._frame_buffer = WebcamFrameBatcher._frame_buffer[-WebcamFrameBatcher._max_buffer_size:]
            LOGGER.warning(
                "WebcamFrameBatcher: Buffer exceeded max size, dropped oldest frames (current: %d)",
                len(WebcamFrameBatcher._frame_buffer)
            )
        
        # If we have enough frames, emit the most recent batch_size frames and clear buffer
        if len(WebcamFrameBatcher._frame_buffer) >= batch_size:
            batch_data = WebcamFrameBatcher._frame_buffer[-batch_size:]
            batch_frames = [tensor for tensor, _ in batch_data]
            
            # Clear buffer after emitting to avoid replaying old frames
            WebcamFrameBatcher._frame_buffer = []
            
            # Check for temporal consistency (detect if frames are out of order)
            timestamps = [ts for _, ts in batch_data]
            if len(timestamps) > 1:
                for i in range(1, len(timestamps)):
                    if timestamps[i] < timestamps[i-1]:
                        LOGGER.warning(
                            "WebcamFrameBatcher: Temporal inconsistency detected! Frame %d is older than frame %d",
                            i, i-1
                        )
            
            # Stack into a single batch tensor
            batch_tensor = torch.cat(batch_frames, dim=0)
            
            # Update last batch time
            WebcamFrameBatcher._last_batch_time = current_time
            
            LOGGER.info(
                "WebcamFrameBatcher: Outputting batch of %d frames (buffer remaining: %d, time_span: %.3fs)",
                batch_size, len(WebcamFrameBatcher._frame_buffer),
                timestamps[-1] - timestamps[0] if len(timestamps) > 1 else 0
            )
            
            return (batch_tensor,)
        else:
            # Not enough frames yet - output the current frame to keep workflow running
            LOGGER.debug(
                "WebcamFrameBatcher: Accumulating frames (%d/%d)",
                len(WebcamFrameBatcher._frame_buffer), batch_size
            )
            return (image,)


def _get_controller(config: NetworkControllerConfig) -> NetworkController:
    if _NETWORK_RUNTIME.controller:
        ctrl = _NETWORK_RUNTIME.controller
        # Clear if stream is dead (ERROR or CLOSED state)
        if ctrl._stream_state in (
            NetworkController.StreamState.ERROR,
            NetworkController.StreamState.CLOSED,
        ):
            LOGGER.info(
                "Clearing dead controller (state=%s, last_error=%s)",
                ctrl._stream_state.value,
                ctrl._last_error or "none",
            )
            _NETWORK_RUNTIME.controller = None
        else:
            ctrl.update_config(config)
            return ctrl
    controller = NetworkController(config)
    _NETWORK_RUNTIME.controller = controller
    return controller


def _get_subscriber(start_seq: int, loop: asyncio.AbstractEventLoop) -> NetworkSubscriber:
    if _NETWORK_RUNTIME.subscriber:
        _NETWORK_RUNTIME.subscriber.attach_loop(loop)
        _NETWORK_RUNTIME.subscriber.config.start_seq = start_seq
        return _NETWORK_RUNTIME.subscriber
    subscriber = NetworkSubscriber(NetworkSubscriberConfig(start_seq=start_seq))
    subscriber.attach_loop(loop)
    _NETWORK_RUNTIME.subscriber = subscriber
    return subscriber


# --- Video stream runtime removed (missing controller) ---

# --- Trickle nodes ---


class TrickleConfig:
    """
    Configuration node for trickle streaming parameters.
    Outputs a config dict that can be connected to Start Trickle Stream.
    """

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "orchestrator_url": ("STRING", {
                    "default": "https://localhost:8935",
                    "tooltip": "Orchestrator URL (e.g., https://hky.eliteencoder.net:8936)",
                }),
                "signer_url": ("STRING", {
                    "default": "https://signer.eliteencoder.net",
                    "tooltip": "Signer URL for authentication",
                }),
                "model_id": ("STRING", {
                    "default": "noop",
                    "tooltip": "Model ID to use (e.g., noop, comfystream)",
                }),
                "fps": ("FLOAT", {
                    "default": 30.0,
                    "min": 1.0,
                    "max": 120.0,
                    "step": 0.1,
                    "tooltip": "Frames per second",
                }),
                "keyframe_interval": ("FLOAT", {
                    "default": 2.0,
                    "min": 0.5,
                    "max": 10.0,
                    "step": 0.1,
                    "tooltip": "Keyframe interval in seconds",
                }),
            },
            "optional": {
                "pipeline_params": ("DICT", {
                    "tooltip": "Connect pipeline config (e.g., StreamDiffusion SDXL params_dict output)",
                }),
            },
        }

    RETURN_TYPES = ("TRICKLE_CONFIG",)
    RETURN_NAMES = ("config",)
    FUNCTION = "create_config"
    CATEGORY = "Trickle"

    def create_config(
        self,
        orchestrator_url: str,
        signer_url: str,
        model_id: str,
        fps: float,
        keyframe_interval: float,
        pipeline_params: Dict[str, Any] = None,
    ) -> tuple:
        config = {
            "orchestrator_url": orchestrator_url,
            "signer_url": signer_url,
            "model_id": model_id,
            "fps": fps,
            "keyframe_interval": keyframe_interval,
            "pipeline_params": pipeline_params or {},
        }
        
        return (config,)


class StartTrickleStream:
    """
    Start a trickle-based stream directly to an orchestrator.
    Requires a TrickleConfig node for connection settings.
    Proactively checks stream health if last execution was over 4 seconds ago.
    Each new stream gets unique trickle URLs from the orchestrator.
    
    IS_CHANGED returns a value based on stream state, so when the stream ends,
    ComfyUI knows to re-execute this node and dependent nodes.
    """

    # If more than this many seconds since last execution, proactively check stream health
    STALE_CHECK_SECONDS = 4.0
    
    # Class-level counter for stream generations - increments when a new stream starts
    _stream_generation: int = 0

    def __init__(self):
        self._status_cache: Optional[tuple[str, str, str, str]] = None
        self._last_execution_time: float = 0.0

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "config": ("TRICKLE_CONFIG",),
            },
            "optional": {
                "width": ("INT", {"default": 512, "min": 64, "max": 4096}),
                "height": ("INT", {"default": 512, "min": 64, "max": 4096}),
                "start_seq": ("INT", {"default": -2}),
                "enabled": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Enable to start/continue streaming. Disable to stop the stream and reset for a new session.",
                }),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("manifest_id", "publish_url", "subscribe_url", "error")
    FUNCTION = "start_trickle_stream"
    CATEGORY = "Trickle"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs) -> str:
        """
        Return a value that changes when stream state changes.
        This tells ComfyUI to re-execute when stream dies or a new stream starts.
        """
        controller = _NETWORK_RUNTIME.controller
        
        if not controller:
            # No controller - check if there was a startup error
            has_error = bool(_NETWORK_RUNTIME.last_startup_error)
            return f"no_stream_{cls._stream_generation}_error={has_error}"
        
        # Check if background tasks are still alive (updates state if dead)
        controller.check_tasks_alive()
        
        state = controller._stream_state
        running = controller.running
        
        # Return a composite value that changes when stream state changes
        # This triggers re-execution when stream dies
        return f"{state.value}_{running}_{cls._stream_generation}"

    def _stop_stream(self):
        """
        Stop the current stream, reset tracking, and return notification.
        Called when enabled=False.
        """
        stream_was_running = False
        
        # Stop the controller if active
        if _NETWORK_RUNTIME.controller:
            controller = _NETWORK_RUNTIME.controller
            stream_was_running = controller.running
            
            LOGGER.info("StartTrickleStream: Stopping stream (user disabled)")
            controller.stop()
            
            # Clear runtime state so next run starts fresh
            _NETWORK_RUNTIME.controller = None
        
        # Stop the subscriber if active
        if _NETWORK_RUNTIME.subscriber:
            try:
                _NETWORK_RUNTIME.subscriber.stop()
            except Exception as exc:
                LOGGER.warning("Failed to stop subscriber: %s", exc)
            _NETWORK_RUNTIME.subscriber = None
        
        # Clear the tracked URL so next start is treated as fresh
        _NETWORK_RUNTIME.last_subscribe_url = None
        
        # Reset both frame bridges to clear old data
        FRAME_BRIDGE.reset()
        TRICKLE_OUTPUT_BRIDGE.reset_sync()
        LOGGER.info("StartTrickleStream: Reset frame bridges (input and output)")
        
        # Reset instance state
        self._status_cache = ("", "", "", "Stream stopped")
        self._last_execution_time = 0.0

        # Reset TrickleFrameInput timing so it doesn't think stream is stale
        TrickleFrameInput._last_frame_time = 0.0

        if stream_was_running:
            LOGGER.info("StartTrickleStream: Stream stopped successfully")
            message = "Stream stopped. Enable to start a new stream."
        else:
            LOGGER.info("StartTrickleStream: No active stream to stop")
            message = "No active stream. Enable to start streaming."

        # Return with UI notification
        return {
            "ui": {"text": [message]},
            "result": self._status_cache,
        }

    def _check_and_clear_stale_stream(self) -> None:
        """
        If enough time has passed since last execution, proactively check if
        the existing stream is still alive. If dead, clear the controller so
        a new stream will be started.
        """
        now = time.perf_counter()
        elapsed = now - self._last_execution_time

        if elapsed > self.STALE_CHECK_SECONDS and _NETWORK_RUNTIME.controller:
            ctrl = _NETWORK_RUNTIME.controller
            state = ctrl._stream_state

            # Check if stream died
            if state in (
                NetworkController.StreamState.ERROR,
                NetworkController.StreamState.CLOSED,
            ):
                LOGGER.info(
                    "StartTrickleStream: Stream stale after %.1fs (state=%s), will start new stream",
                    elapsed,
                    state.value,
                )
                _NETWORK_RUNTIME.controller = None
                _NETWORK_RUNTIME.subscriber = None
            elif not ctrl.is_healthy():
                # Also check is_healthy for edge cases (e.g., STARTING but grace period expired)
                health = ctrl.get_health()
                LOGGER.info(
                    "StartTrickleStream: Stream unhealthy after %.1fs (state=%s, error=%s), will start new stream",
                    elapsed,
                    state.value,
                    health.get("last_error", ""),
                )
                _NETWORK_RUNTIME.controller = None
                _NETWORK_RUNTIME.subscriber = None

    def start_trickle_stream(
        self,
        config: Dict[str, Any],
        width: int = 512,
        height: int = 512,
        start_seq: int = -2,
        enabled: bool = True,
    ):
        # When disabled, stop the stream and reset state
        if not enabled:
            return self._stop_stream()

        # Proactively check for stale/dead streams before proceeding
        self._check_and_clear_stale_stream()

        # Extract values from config
        orchestrator_url = config.get("orchestrator_url", "https://localhost:8935")
        signer_url = config.get("signer_url", "")
        model_id = config.get("model_id", "noop")
        fps = config.get("fps", 30.0)
        keyframe_interval = config.get("keyframe_interval", 2.0)
        
        # Pipeline params passed through from TrickleConfig
        pipeline_params = config.get("pipeline_params", {})

        resolved_orch, resolved_signer = resolve_network_config(orchestrator_url, signer_url)
        controller_config = NetworkControllerConfig(
            orchestrator_url=resolved_orch,
            signer_url=resolved_signer or None,
            model_id=model_id,
            fps=float(fps),
            frame_width=width,
            frame_height=height,
            keyframe_interval_s=float(keyframe_interval),

        )
        controller = _get_controller(controller_config)

        # Validate stream state before reusing
        # Force restart if stream is dead/closed/idle, or if not healthy
        needs_restart = False
        if controller._stream_state in (
            NetworkController.StreamState.ERROR,
            NetworkController.StreamState.CLOSED,
            NetworkController.StreamState.IDLE,
        ):
            LOGGER.info(
                "StartTrickleStream: Stream state=%s, forcing restart",
                controller._stream_state.value,
            )
            needs_restart = True
        elif not controller.is_healthy():
            health = controller.get_health()
            LOGGER.info(
                "StartTrickleStream: Stream unhealthy (state=%s, error=%s), forcing restart",
                controller._stream_state.value,
                health.get("last_error", ""),
            )
            needs_restart = True
        elif controller.running:
            # Stream is running and healthy - reuse existing stream
            LOGGER.debug(
                "StartTrickleStream: Reusing healthy stream (state=%s, frames_sent=%d)",
                controller._stream_state.value,
                controller.frames_sent,
            )
            status = controller.status()
            health = controller.get_health()

            # Ensure subscriber is running if we have a subscribe_url
            subscribe_url = status.get("subscribe_url")
            if subscribe_url:
                subscriber = _get_subscriber(start_seq, controller.loop)
                if not subscriber.task_alive:
                    task_error = subscriber.check_task_exception()
                    LOGGER.info(
                        "StartTrickleStream: Subscriber task not alive (error=%s), restarting",
                        task_error or "none",
                    )
                    subscriber.start(subscribe_url)

            # Skip to output - don't restart
            needs_restart = False
        else:
            needs_restart = True

        if needs_restart:
            # Note: Don't reset TRICKLE_OUTPUT_BRIDGE here - that would cause black frames
            # during startup. The subscriber handles resetting when the URL changes (i.e.,
            # when connecting to a genuinely different stream). For same-stream reconnects,
            # keeping the last frame is better UX than showing black.
            
            try:
                status = controller.start(
                    model_id=model_id,
                    params=pipeline_params,
                )
                health = controller.get_health()

                # Increment stream generation so IS_CHANGED reflects the new stream
                StartTrickleStream._stream_generation += 1

                # Clear any previous startup error since we succeeded
                _NETWORK_RUNTIME.last_startup_error = None

                LOGGER.info(
                    "StartTrickleStream: New stream started (generation=%d, publish_url=%s)",
                    StartTrickleStream._stream_generation,
                    status.get("publish_url", "")[:50] + "...",
                )

                # Start subscriber if subscribe_url is present (only on restart)
                new_subscribe_url = status.get("subscribe_url")
                if new_subscribe_url:
                    # Check if this is a different stream than before - if so, clear old frames
                    if _NETWORK_RUNTIME.last_subscribe_url and _NETWORK_RUNTIME.last_subscribe_url != new_subscribe_url:
                        LOGGER.info(
                            "StartTrickleStream: Subscribe URL changed, clearing stale output frames"
                        )
                        TRICKLE_OUTPUT_BRIDGE.reset_sync()
                    _NETWORK_RUNTIME.last_subscribe_url = new_subscribe_url
                    
                    subscriber = _get_subscriber(start_seq, controller.loop)
                    subscriber.start(new_subscribe_url)
            except Exception as exc:
                # Handle connection/timeout errors gracefully
                error_str = str(exc)
                LOGGER.error("StartTrickleStream: Failed to start stream: %s", error_str)

                # Properly stop and clean up the controller before clearing
                if controller:
                    try:
                        controller.stop()
                    except Exception as stop_exc:
                        LOGGER.warning("Failed to stop controller during error cleanup: %s", stop_exc)

                # Reset the frame bridge to clear old loop bindings
                FRAME_BRIDGE.reset()

                # Clear the controller so next execution tries fresh
                _NETWORK_RUNTIME.controller = None
                _NETWORK_RUNTIME.subscriber = None
                self._status_cache = None

                # Track the startup error so other nodes know why there's no stream
                error_msg = f"Failed to start stream: {error_str}"
                _NETWORK_RUNTIME.last_startup_error = error_msg

                self._status_cache = ("", "", "", error_msg)
                # Return with UI notification so error is visible
                return {
                    "ui": {"text": [f"ERROR: {error_msg}"]},
                    "result": self._status_cache,
                }

        # Update tracking state
        self._last_execution_time = time.perf_counter()

        manifest_id = status.get("manifest_id", "")
        publish_url = status.get("publish_url", "")
        subscribe_url = status.get("subscribe_url", "")
        error_msg = "" if controller.is_healthy() else health.get("last_error", "")
        self._status_cache = (manifest_id, publish_url, subscribe_url, error_msg)

        # Return with success notification if stream started successfully
        if needs_restart and not error_msg:
            return {
                "ui": {"text": [f"Stream connected: {manifest_id}"]},
                "result": self._status_cache,
            }

        return self._status_cache


class LoadVideoStream:
    """
    Start a trickle stream and immediately stream frames from a VIDEO input
    (e.g., ComfyUI's Load Video node). Automatically resolves the underlying
    file path, probes dimensions/FPS, and feeds frames into the publisher.
    """

    def __init__(self):
        self._starter = StartTrickleStream()

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "config": ("TRICKLE_CONFIG",),
                "video": ("VIDEO",),
            },
            "optional": {
                "width": ("INT", {"default": 0, "min": 0, "max": 4096}),
                "height": ("INT", {"default": 0, "min": 0, "max": 4096}),
                "start_seq": ("INT", {"default": -2}),
                "loop": ("BOOLEAN", {"default": True}),
                "fps_override": ("FLOAT", {
                    "default": 0.0,
                    "min": 0.0,
                    "max": 240.0,
                    "step": 0.1,
                    "tooltip": "Override video FPS. 0 uses source FPS.",
                }),
                "enabled": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("manifest_id", "publish_url", "subscribe_url", "error")
    FUNCTION = "start_stream_from_video"
    CATEGORY = "Trickle"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs) -> str:
        return StartTrickleStream.IS_CHANGED(**kwargs)

    @staticmethod
    def _probe_video(video_path: str) -> tuple[int, int, float]:
        """
        Read video metadata (width, height, fps). Returns zeros if unavailable.
        """
        try:
            capture = cv2.VideoCapture(video_path)
            if not capture.isOpened():
                return 0, 0, 0.0
            width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            capture.release()
            return width, height, fps
        except Exception:
            return 0, 0, 0.0

    def _stop_stream(self):
        """
        Stop the video stream and file feed, mirroring StartTrickleStream behavior.
        """
        # Stop file feed first (before stopping the stream)
        controller = _NETWORK_RUNTIME.controller
        if controller:
            controller.stop_file_feed()
        
        _NETWORK_RUNTIME.last_subscribe_url = None
        
        # Reset both frame bridges to clear old data
        FRAME_BRIDGE.reset()
        TRICKLE_OUTPUT_BRIDGE.reset_sync()
        LOGGER.info("StartTrickleStream: Reset frame bridges (input and output)")
        
        # Delegate to StartTrickleStream's stop logic for full cleanup
        return self._starter._stop_stream()


    def start_stream_from_video(
        self,
        config: Dict[str, Any],
        video: Any,
        width: int = 0,
        height: int = 0,
        start_seq: int = -2,
        loop: bool = True,
        fps_override: float = 0.0,
        enabled: bool = True,
    ):
        if not enabled:
            return self._stop_stream()

        resolved_path = TrickleVideoInput._resolve_video_path(video)
        if not resolved_path:
            error_msg = "Video path is required"
            LOGGER.error("LoadVideoStream: %s (input=%r)", error_msg, video)
            return {
                "ui": {"text": [f"ERROR: {error_msg}"]},
                "result": ("", "", "", error_msg),
            }
        if not os.path.exists(resolved_path):
            error_msg = f"Video not found: {resolved_path}"
            LOGGER.error("LoadVideoStream: %s", error_msg)
            return {
                "ui": {"text": [f"ERROR: {error_msg}"]},
                "result": ("", "", "", error_msg),
            }

        probed_width, probed_height, source_fps = self._probe_video(resolved_path)
        effective_width = width or probed_width or 512
        effective_height = height or probed_height or 512

        status = self._starter.start_trickle_stream(
            config,
            effective_width,
            effective_height,
            start_seq,
            True,
        )
        # If start_trickle_stream returned a dict with error UI, pass it through
        if isinstance(status, dict) and "ui" in status:
            return status

        status_values = status.get("result", None) if isinstance(status, dict) else status
        if not status_values:
            status_values = ("", "", "", "")

        manifest_id, publish_url, subscribe_url, error_msg = status_values

        controller = _NETWORK_RUNTIME.controller
        if controller and controller.running and not error_msg:
            feed_fps = fps_override if fps_override > 0 else source_fps
            try:
                controller.start_file_feed(
                    resolved_path,
                    loop_video=bool(loop),
                    fps_override=feed_fps if feed_fps > 0 else None,
                )
                LOGGER.info("LoadVideoStream: started file feed for %s", resolved_path)
            except Exception as exc:
                feed_error = f"file feed error: {exc}"
                LOGGER.error("LoadVideoStream: %s", feed_error)
                error_msg = f"{error_msg}; {feed_error}" if error_msg else feed_error
                return {
                    "ui": {"text": [f"ERROR: {error_msg}"]},
                    "result": (manifest_id, publish_url, subscribe_url, error_msg),
                }

        # Success - return with notification
        video_name = os.path.basename(resolved_path)
        success_msg = f"Stream started: {video_name}"
        return {
            "ui": {"text": [success_msg]},
            "result": (manifest_id, publish_url, subscribe_url, error_msg),
        }


class TrickleFrameInput:
    """
    Enqueue frames into the trickle publisher queue.
    Connect the publish_url output from StartTrickleStream to ensure correct execution order.
    """
    
    # Class-level tracking for proactive health checks
    _last_frame_time: float = 0.0
    HEALTH_CHECK_INTERVAL_SECONDS: float = 4.0

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "image": ("IMAGE",),
            },
            "optional": {
                "publish_url": ("STRING", {
                    "default": "",
                    "tooltip": "Connect from StartTrickleStream to ensure stream starts first",
                }),
                "enabled": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ()
    RETURN_NAMES = ()
    FUNCTION = "push_frame"
    CATEGORY = "Trickle"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs) -> bool:
        return True

    def push_frame(self, image: torch.Tensor, publish_url: str = "", enabled: bool = True):
        if enabled:
            controller = _NETWORK_RUNTIME.controller
            
            # No controller - stream not started or failed to start
            if not controller:
                # Check if there was a startup error
                if _NETWORK_RUNTIME.last_startup_error:
                    LOGGER.error(
                        "TrickleFrameInput: No stream available - %s",
                        _NETWORK_RUNTIME.last_startup_error,
                    )
                    raise RuntimeError(
                        f"Trickle stream failed to start: {_NETWORK_RUNTIME.last_startup_error}. "
                        "Check orchestrator connection and try again."
                    )
                else:
                    # No error but no controller - likely execution order issue
                    LOGGER.error(
                        "TrickleFrameInput: No stream started yet. "
                        "Connect publish_url from StartTrickleStream to ensure correct order."
                    )
                    raise RuntimeError(
                        "Trickle stream not started. Connect the 'publish_url' output from "
                        "StartTrickleStream to TrickleFrameInput before sending frames."
                    )
            
            # Proactive health check if enough time has passed since last frame
            now = time.perf_counter()
            elapsed_since_last = now - TrickleFrameInput._last_frame_time
            
            if elapsed_since_last > self.HEALTH_CHECK_INTERVAL_SECONDS:
                # Check if background tasks are still alive
                tasks_alive = controller.check_tasks_alive()
                if not tasks_alive:
                    health = controller.get_health()
                    error_msg = health.get("last_error", "stream tasks exited")
                    LOGGER.error(
                        "TrickleFrameInput: Stream died (detected via task check after %.1fs gap): %s",
                        elapsed_since_last, error_msg,
                    )
                    raise RuntimeError(
                        f"Trickle stream ended: {error_msg}. "
                        "Re-run workflow to start a new stream."
                    )
            
            # Check stream state for more specific handling
            state = controller._stream_state
            running = controller.running
            
            # Log state for debugging (only occasionally to avoid spam)
            if controller.frames_sent % 30 == 0:
                LOGGER.debug(
                    "TrickleFrameInput: state=%s running=%s frames_sent=%d",
                    state.value, running, controller.frames_sent,
                )
            
            if state == NetworkController.StreamState.IDLE:
                LOGGER.warning(
                    "TrickleFrameInput: Stream in IDLE state, dropping frame. "
                    "Connect publish_url from StartTrickleStream to ensure correct order."
                )
                return ()
            
            # Stream is dead - raise error to stop workflow
            if state in (NetworkController.StreamState.ERROR, NetworkController.StreamState.CLOSED):
                health = controller.get_health()
                error_msg = health.get("last_error", "")
                LOGGER.error(
                    "TrickleFrameInput: Stream ended (state=%s, error=%s)",
                    state.value, error_msg,
                )
                raise RuntimeError(
                    f"Trickle stream ended (state={state.value}): {error_msg or 'stream closed'}. "
                    "Re-run workflow to start a new stream."
                )
            
            # Also check running flag - if False but state not ERROR/CLOSED, stream died unexpectedly
            if not running and state not in (NetworkController.StreamState.STARTING,):
                health = controller.get_health()
                error_msg = health.get("last_error", "")
                LOGGER.error(
                    "TrickleFrameInput: Stream not running (state=%s, error=%s)",
                    state.value, error_msg,
                )
                raise RuntimeError(
                    f"Trickle stream stopped (state={state.value}): {error_msg or 'publisher stopped'}. "
                    "Re-run workflow to start a new stream."
                )
            
            # STARTING, RUNNING, DEGRADED states - check is_healthy for grace period logic
            if state == NetworkController.StreamState.STARTING:
                # Allow frames during startup grace period
                if not controller.is_healthy():
                    health = controller.get_health()
                    error_msg = health.get("last_error", "")
                    raise RuntimeError(
                        f"Trickle stream failed to start: {error_msg or 'startup timeout'}. "
                        "Re-run workflow to try again."
                    )
            
            frames_enqueued = enqueue_tensor_batch(image)
            
            # Update last frame time for health check interval tracking
            TrickleFrameInput._last_frame_time = time.perf_counter()
            
            LOGGER.debug(
                "Trickle frames enqueued=%d (loop_ready=%s depth=%s state=%s)",
                frames_enqueued,
                has_loop(),
                queue_depth(),
                state.value,
            )
        return ()


class TrickleVideoInput:
    """
    Feed a VIDEO input into the trickle publisher by enqueueing frames from file.
    Relies on an already running StartTrickleStream controller; re-execution will
    reuse the existing publisher without interruption if the video/config matches.
    Connect the publish_url output from StartTrickleStream to ensure correct execution order.
    """

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "video": ("VIDEO",),
            },
            "optional": {
                "publish_url": ("STRING", {
                    "default": "",
                    "tooltip": "Connect from StartTrickleStream to ensure stream starts first",
                }),
                "loop": ("BOOLEAN", {"default": True, "tooltip": "Loop video when it ends"}),
                "fps_override": ("FLOAT", {
                    "default": 0.0,
                    "min": 0.0,
                    "max": 240.0,
                    "step": 0.1,
                    "tooltip": "Optional FPS override; 0 uses source FPS",
                }),
                "enabled": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ()
    RETURN_NAMES = ()
    FUNCTION = "feed_video"
    CATEGORY = "Trickle"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs) -> bool:
        # Always re-executes to allow connecting after stream starts
        return True

    def feed_video(
        self,
        video: Any,
        publish_url: str = "",
        loop: bool = True,
        fps_override: float = 0.0,
        enabled: bool = True,
    ):
        if not enabled:
            LOGGER.info("TrickleVideoInput: Disabled, skipping")
            return ()

        LOGGER.info("TrickleVideoInput: feed_video called with video type=%s", type(video).__name__)

        controller = _NETWORK_RUNTIME.controller
        if not controller:
            LOGGER.warning("TrickleVideoInput: No running stream; connect publish_url from StartTrickleStream")
            return ()
        if not controller.running or not controller.is_healthy():
            LOGGER.warning("TrickleVideoInput: Stream not healthy/running (state=%s); skipping feed", controller._stream_state.value)
            return ()

        resolved_path = self._resolve_video_path(video)
        if not resolved_path:
            LOGGER.error("TrickleVideoInput: Could not resolve video path from VIDEO input")
            LOGGER.error("TrickleVideoInput: VIDEO input type=%s repr=%r", type(video).__name__, video)
            # Log all attributes at INFO level for debugging
            all_attrs = [a for a in dir(video) if not a.startswith("__")]
            LOGGER.error("TrickleVideoInput: VideoFromFile all attributes: %s", all_attrs)
            obj_dict = getattr(video, "__dict__", {})
            LOGGER.error("TrickleVideoInput: VideoFromFile __dict__: %s", obj_dict)
            return ()

        LOGGER.info("TrickleVideoInput: Starting video feed from %s (loop=%s, fps_override=%s)", resolved_path, loop, fps_override)

        try:
            controller.start_file_feed(
                resolved_path,
                loop_video=bool(loop),
                fps_override=fps_override if fps_override > 0 else None,
            )
            LOGGER.info("TrickleVideoInput: Video feed started successfully")
        except Exception as exc:
            LOGGER.error("TrickleVideoInput: Failed to start video feed: %s", exc)
        return ()

    @staticmethod
    def _resolve_video_path(video_input: Any) -> str:
        if video_input is None:
            return ""
        if isinstance(video_input, str):
            return video_input
        if isinstance(video_input, dict):
            # Try common keys used by video loaders
            for key in ("video", "path", "file", "filename", "video_path", "source"):
                val = video_input.get(key)
                if isinstance(val, str) and val:
                    return val
            # Some loaders nest the path in a dict
            for key in ("video_info", "meta", "metadata"):
                nested = video_input.get(key)
                if isinstance(nested, dict):
                    for subkey in ("path", "file", "filename", "video_path", "source"):
                        val = nested.get(subkey)
                        if isinstance(val, str) and val:
                            return val

        # Handle ComfyUI VideoFromFile object - check all attributes
        for attr in ("path", "file", "filename", "filepath", "video_path", "source", "video", "file_path", "_path", "_file"):
            val = getattr(video_input, attr, None)
            if isinstance(val, str) and val:
                return val

        # Check if it has a get_path() or similar method
        for method in ("get_path", "get_file", "get_filename", "to_path", "as_path"):
            func = getattr(video_input, method, None)
            if callable(func):
                try:
                    val = func()
                    if isinstance(val, str) and val:
                        return val
                except Exception:
                    pass

        # Check __dict__ for any path-like attributes
        obj_dict = getattr(video_input, "__dict__", {})
        if obj_dict:
            LOGGER.debug("TrickleVideoInput: VideoFromFile __dict__ keys: %s", list(obj_dict.keys()))
            for key, val in obj_dict.items():
                if isinstance(val, str) and ("path" in key.lower() or "file" in key.lower()):
                    return val

        # Log all attributes for debugging
        all_attrs = [a for a in dir(video_input) if not a.startswith("__")]
        LOGGER.debug("TrickleVideoInput: VideoFromFile attributes: %s", all_attrs)

        return ""


class TrickleFrameOutput:
    """
    Retrieve the latest decoded frame from the trickle subscriber.
    
    The subscriber is automatically started by StartTrickleStream and runs in
    the background, storing the latest output frame in a shared bridge.
    This node returns the most recent frame and displays a preview.
    """

    def __init__(self):
        self._blank = self._blank_tensor()
        self._output_dir = folder_paths.get_temp_directory()
        self._type = "temp"
        self._prefix = "trickle_output"

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "optional": {},
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "pull_frame"
    CATEGORY = "Trickle"
    OUTPUT_NODE = True  # Always executes and shows preview

    @classmethod
    def IS_CHANGED(cls, **kwargs) -> float:
        # NaN != NaN, so this always triggers re-execution
        return float("nan")

    def pull_frame(self):
        """Pull the latest frame from the trickle subscriber (synchronous)."""
        from PIL import Image
        
        # Check if subscriber is active
        subscriber = _NETWORK_RUNTIME.subscriber
        if not subscriber:
            return self._return_with_preview(self._blank)
        
        # Check if task has crashed
        task_error = subscriber.check_task_exception()
        if task_error:
            LOGGER.warning("TrickleFrameOutput: Subscriber task crashed: %s", task_error)
        
        if not subscriber.running and not subscriber.task_alive:
            return self._return_with_preview(self._blank)
        
        try:
            frame_np, timestamp, has_frame = TRICKLE_OUTPUT_BRIDGE.get_frame_or_blank_sync()
            tensor = torch.from_numpy(frame_np.astype(np.float32) / 255.0).unsqueeze(0)
            return self._return_with_preview(tensor)
        except Exception as exc:
            LOGGER.error("TrickleFrameOutput error: %s", exc)
            return self._return_with_preview(self._blank)

    def _return_with_preview(self, tensor: torch.Tensor):
        """Return tensor with UI preview."""
        from PIL import Image
        import uuid
        
        results = []
        for img_tensor in tensor:
            # Convert tensor to PIL Image
            img_np = (img_tensor.cpu().numpy() * 255).astype(np.uint8)
            img = Image.fromarray(img_np)
            
            # Save to temp directory
            filename = f"{self._prefix}_{uuid.uuid4().hex[:8]}.png"
            filepath = os.path.join(self._output_dir, filename)
            img.save(filepath, compress_level=1)
            
            results.append({
                "filename": filename,
                "subfolder": "",
                "type": self._type,
            })
        
        return {
            "ui": {"images": results},
            "result": (tensor,),
        }

    @staticmethod
    def _blank_tensor(width: int = 512, height: int = 512) -> torch.Tensor:
        blank = torch.zeros((height, width, 3), dtype=torch.float32)
        return blank.unsqueeze(0)


class UpdateTrickleStream:
    """
    Send control messages to the running trickle stream (if supported).
    """

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "control_payload": ("DICT",),
            },
            "optional": {
                "enabled": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ()
    RETURN_NAMES = ()
    FUNCTION = "update_trickle_stream"
    CATEGORY = "Trickle"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return True

    def update_trickle_stream(self, control_payload: Dict[str, Any], enabled: bool = True):
        if not enabled:
            return ()
        controller = _NETWORK_RUNTIME.controller
        if not controller or not controller.job or not controller.job.control:
            LOGGER.warning("No active trickle stream control channel available")
            return ()
        try:
            future = asyncio.run_coroutine_threadsafe(
                controller.job.control.write_control(control_payload),
                controller.loop,
            )
            future.result(timeout=5)
            return ()
        except Exception as exc:
            LOGGER.error("Failed to send control payload: %s", exc)
            return ()


# Register trickle nodes into the mapping dictionaries
NODE_CLASS_MAPPINGS = {
    "WebcamFrameBatcher": WebcamFrameBatcher,
    "TrickleConfig": TrickleConfig,
    "TrickleFrameInput": TrickleFrameInput,
    "TrickleFrameOutput": TrickleFrameOutput,
    "LoadVideoStream": LoadVideoStream,
    "StartTrickleStream": StartTrickleStream,
    "UpdateTrickleStream": UpdateTrickleStream,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "WebcamFrameBatcher": "Webcam Frame Batcher",
    "TrickleConfig": "Trickle Config",
    "TrickleFrameInput": "Trickle Frame Input",
    "TrickleFrameOutput": "Trickle Frame Output",
    "LoadVideoStream": "Load Video Stream",
    "StartTrickleStream": "Start Trickle Stream",
    "UpdateTrickleStream": "Update Trickle Stream",
}
