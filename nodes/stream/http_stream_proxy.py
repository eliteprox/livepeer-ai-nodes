"""
HTTP proxy server that forwards trickle subscribe streams as MPEG-TS.
Converts trickle protocol to standard video stream playable in VLC, MPV, etc.
"""
import asyncio
import logging
from typing import Optional

from aiohttp import web
from livepeer_gateway.media_output import MediaOutput

LOGGER = logging.getLogger("comfyui_trickle.http_stream_proxy")


class HttpStreamProxy:
    """
    HTTP server that proxies a trickle subscribe URL as MPEG-TS.
    Runs on a dedicated asyncio loop in a background thread.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765
    ):
        self.host = host
        self.port = port
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._current_subscribe_url: Optional[str] = None

    async def start(self) -> str:
        """Start the HTTP server and return the base URL."""
        if self._runner:
            LOGGER.info("HttpStreamProxy already running at http://%s:%d", self.host, self.port)
            print(f"[HTTP_PROXY] Already running at http://{self.host}:{self.port}")
            return f"http://{self.host}:{self.port}"

        self._app = web.Application()
        self._app.router.add_get("/stream", self._handle_stream)
        self._app.router.add_get("/health", self._handle_health)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()

        url = f"http://{self.host}:{self.port}"
        LOGGER.info("HttpStreamProxy started at %s", url)
        print(f"[HTTP_PROXY] Server started at {url}")
        print(f"[HTTP_PROXY] Stream endpoint: {url}/stream")
        print(f"[HTTP_PROXY] Open in VLC: Media -> Open Network Stream -> {url}/stream")
        return url

    async def stop(self) -> None:
        """Stop the HTTP server."""
        if self._site:
            await self._site.stop()
            self._site = None

        if self._runner:
            await self._runner.cleanup()
            self._runner = None

        self._app = None
        LOGGER.info("HttpStreamProxy stopped")

    def set_subscribe_url(self, subscribe_url: str) -> None:
        """Update the trickle subscribe URL to proxy."""
        self._current_subscribe_url = subscribe_url
        print(f"[HTTP_PROXY] Updated subscribe URL")
        print(f"[HTTP_PROXY] Ready to stream at: http://{self.host}:{self.port}/stream")
        LOGGER.info("HttpStreamProxy now proxying: %s", subscribe_url)

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        status = {
            "status": "ok",
            "proxying": self._current_subscribe_url is not None,
            "subscribe_url": self._current_subscribe_url or "",
        }
        return web.json_response(status)

    async def _handle_stream(self, request: web.Request) -> web.StreamResponse:
        """
        Stream endpoint that proxies the trickle subscribe URL.
        Uses MediaOutput to properly decode trickle segments and forward as MPEG-TS.
        """
        if not self._current_subscribe_url:
            print("[HTTP_PROXY] ERROR: No stream configured")
            return web.Response(status=404, text="No stream configured")

        print(f"[HTTP_PROXY] Client connected to /stream")
        LOGGER.info("Client connected to /stream")

        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "video/mp2t",  # MPEG-TS
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        await response.prepare(request)

        try:
            # Use MediaOutput to properly decode trickle segments
            print(f"[HTTP_PROXY] Starting MPEG-TS stream...")
            media_output = MediaOutput(
                self._current_subscribe_url,
                start_seq=-2,
                max_retries=5,
                chunk_size=64 * 1024,
            )

            # Stream MPEG-TS bytes from trickle to client
            bytes_sent = 0
            chunk_count = 0
            async for chunk in media_output.bytes():
                if not chunk:
                    break
                await response.write(chunk)
                bytes_sent += len(chunk)
                chunk_count += 1
                
                # Log every 100 chunks (~6.4MB)
                if chunk_count % 100 == 0:
                    print(f"[HTTP_PROXY] Streamed {bytes_sent / 1024 / 1024:.2f} MB")

            print(f"[HTTP_PROXY] Stream ended ({bytes_sent / 1024 / 1024:.2f} MB total)")
            LOGGER.info("Stream ended cleanly (sent %d bytes)", bytes_sent)

        except asyncio.CancelledError:
            print(f"[HTTP_PROXY] Client disconnected")
            LOGGER.info("Client disconnected from /stream")
        except Exception as exc:
            print(f"[HTTP_PROXY] ERROR: {exc}")
            LOGGER.error("Stream proxy error: %s", exc, exc_info=True)
        finally:
            await response.write_eof()

        return response

    @property
    def running(self) -> bool:
        """Check if server is running."""
        return self._runner is not None and self._site is not None

    @property
    def stream_url(self) -> str:
        """Get the full stream URL for players."""
        return f"http://{self.host}:{self.port}/stream"


# Global singleton proxy instance
_STREAM_PROXY: Optional[HttpStreamProxy] = None


async def get_stream_proxy(host: str = "127.0.0.1", port: int = 8765) -> HttpStreamProxy:
    """Get or create the global stream proxy instance."""
    global _STREAM_PROXY

    if _STREAM_PROXY is None:
        _STREAM_PROXY = HttpStreamProxy(host=host, port=port)

    if not _STREAM_PROXY.running:
        await _STREAM_PROXY.start()

    return _STREAM_PROXY
