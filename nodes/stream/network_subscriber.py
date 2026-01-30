from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
from livepeer_gateway.media_output import MediaOutput

from .trickle_output_bridge import TRICKLE_OUTPUT_BRIDGE

LOGGER = logging.getLogger("comfyui_trickle.network_subscriber")


@dataclass
class NetworkSubscriberConfig:
    start_seq: int = -2
    chunk_size: int = 256 * 1024
    max_retries: int = 5


class NetworkSubscriber:
    """
    Trickle subscriber that pulls output frames from the orchestrator and stores
    the latest frame in the shared TRICKLE_OUTPUT_BRIDGE for consumption by ComfyUI nodes.

    Follows the pattern from livepeer-python-gateway examples:
    - Uses MediaOutput for trickle subscription
    - Uses latest_video_frames() to always get the freshest frame (skipping buffered ones)
    - Decodes frames and stores them in a thread-safe bridge
    """

    def __init__(self, config: Optional[NetworkSubscriberConfig] = None):
        self.config = config or NetworkSubscriberConfig()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._task: Optional[asyncio.Future] = None
        self._running = False
        self.frames_received = 0
        self.frames_skipped = 0

    @property
    def running(self) -> bool:
        """Whether the subscriber is currently running."""
        return self._running

    @property
    def task_alive(self) -> bool:
        """Whether the background task is still running (not done/cancelled)."""
        if self._task is None:
            return False
        return not self._task.done()

    def check_task_exception(self) -> str:
        """Check if the task has crashed and return error message if so."""
        if self._task is None:
            return ""
        if not self._task.done():
            return ""
        try:
            # This will raise if there was an exception
            self._task.result()
            return ""
        except asyncio.CancelledError:
            return "cancelled"
        except Exception as exc:
            return f"error: {exc}"

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        print(f"[SUBSCRIBER] Attaching to loop: {loop}, running={loop.is_running() if loop else 'N/A'}")
        self.loop = loop

    def start(self, subscribe_url: str) -> None:
        if not self.loop:
            raise RuntimeError("NetworkSubscriber requires an attached asyncio loop")
        self.stop()
        
        # Note: URL change detection and bridge reset is now handled at the runtime level
        # in frame_nodes.py (StartTrickleStream) before calling this method.
        
        self._running = True
        self.frames_received = 0
        self.frames_skipped = 0
        print(f"[SUBSCRIBER] Starting subscriber for {subscribe_url}")
        LOGGER.info("Starting trickle subscriber for %s", subscribe_url)
        self._task = asyncio.run_coroutine_threadsafe(
            self._consume(subscribe_url), self.loop
        )
        print(f"[SUBSCRIBER] Task submitted to loop: {self._task}")

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _consume(self, subscribe_url: str) -> None:
        """
        Consume frames from the trickle subscriber and store them in the bridge.
        Uses latest_video_frames() to always get the freshest frame.
        """
        # Use print() to ensure visibility in console regardless of log level
        print(f"[SUBSCRIBER] Connecting to {subscribe_url} (start_seq={self.config.start_seq})")
        LOGGER.info(
            "Subscriber connecting to %s (start_seq=%d)",
            subscribe_url,
            self.config.start_seq,
        )
        try:
            output = MediaOutput(
                subscribe_url,
                start_seq=self.config.start_seq,
                max_retries=self.config.max_retries,
                chunk_size=self.config.chunk_size,
            )
            print("[SUBSCRIBER] MediaOutput created, starting to consume frames...")
            LOGGER.info("Subscriber MediaOutput created, starting to consume frames...")
            
            # Use latest_video_frames() to skip buffered frames and always get the newest
            async for decoded in output.latest_video_frames():
                if not self._running:
                    print("[SUBSCRIBER] Stopped by running flag")
                    LOGGER.info("Subscriber stopped by running flag")
                    break
                
                try:
                    frame = decoded.frame.to_ndarray(format="rgb24")
                    # Use thread-safe sync method since bridge is accessed from multiple threads
                    TRICKLE_OUTPUT_BRIDGE.put_frame_sync(np.array(frame))
                    self.frames_received += 1
                    
                    # Log every frame at INFO level so we can see subscriber activity
                    if self.frames_received % 30 == 1:  # Log every 30 frames (about 1 second at 30fps)
                        print(f"[SUBSCRIBER] Received frame {self.frames_received} ({decoded.width}x{decoded.height})")
                        LOGGER.info(
                            "Subscriber received frame %d (%sx%s pts=%s)",
                            self.frames_received,
                            decoded.width,
                            decoded.height,
                            decoded.pts,
                        )
                except Exception as frame_exc:
                    print(f"[SUBSCRIBER] Frame processing error: {frame_exc}")
                    LOGGER.error("Subscriber failed to process frame: %s", frame_exc, exc_info=True)
                    continue
            
            print(f"[SUBSCRIBER] Finished consuming frames (total={self.frames_received})")
            LOGGER.info("Subscriber finished consuming frames (total=%d)", self.frames_received)
        except asyncio.CancelledError:
            print(f"[SUBSCRIBER] Cancelled (received {self.frames_received} frames)")
            LOGGER.info("Subscriber cancelled (received %d frames)", self.frames_received)
        except Exception as exc:
            print(f"[SUBSCRIBER] ERROR: {exc}")
            LOGGER.error("Subscriber error: %s", exc, exc_info=True)
        finally:
            self._running = False
            print("[SUBSCRIBER] _consume exited")
            LOGGER.info("Subscriber _consume exited")
