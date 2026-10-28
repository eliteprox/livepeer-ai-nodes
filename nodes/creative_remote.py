"""Account-authenticated API nodes for bounded remote frame and image jobs.

SD Turbo / Klein expose streaming sessions, not a batch image endpoint. The
batch adapter below sends each image through an isolated frame exchange in a
single leased session. Live mode publishes a bounded clip at the chosen FPS.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
from fractions import Fraction
from urllib.parse import urlparse

import aiohttp
import av
import numpy as np
import torch
from PIL import Image
from livepeer_gateway.live_runner import LiveRunnerSession, stop_runner_session
from livepeer_gateway.media_output import MediaOutput
from livepeer_gateway.media_publish import MediaPublish, MediaPublishConfig, VideoOutputConfig
from livepeer_gateway.remote_signer import LivePaymentChallenge, LivePaymentSession, get_signer_info
from livepeer_gateway.selection import runner_selector

from .pymthouse_session import stream_signer

DISCOVERY = "https://ai1.eliteencoder.net:8936/discovery"
APPS = ["comfystream/sd-turbo", "comfystream/flux-klein"]


def _https(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Runner endpoints must use HTTPS")
    return url.rstrip("/")


async def _json(http, url, body=None, headers=None):
    async with http.request("POST" if body is not None else "GET", _https(url),
                            json=body, headers=headers) as response:
        data = await response.json(content_type=None)
        if response.status >= 400:
            raise RuntimeError(f"Remote HTTP {response.status}: {data}")
        return data


async def _discover(http, app):
    entries = await _json(http, DISCOVERY)
    candidates = [r for orch in entries for r in orch.get("runners", [])
                  if r.get("app") == app and r.get("capacity_available", 0) > 0]
    if not candidates:
        raise RuntimeError(f"No available capacity for {app}")
    return candidates[0]


async def _reserve_pixels(http, runner, signer, headers):
    """Use the runner's pixel payment challenge; never convert it to seconds."""
    price = runner.get("price_info", {})
    if price.get("unit") != "720p-pixel-seconds":
        raise RuntimeError("Unexpected pricing unit for realtime image runner")
    material = await get_signer_info(signer, frozenset(headers.items()))
    request_headers = {"Livepeer-Payer-Address": material.address}
    payment = None
    for _ in range(2):
        async with http.post(_https(runner["url"]), json={}, headers=request_headers) as response:
            data = await response.json(content_type=None)
            if response.status == 402 and payment is None:
                challenge = LivePaymentChallenge(data["payment_params"], data["manifest_id"], data["payment_url"])
                payment = LivePaymentSession(signer, signer_headers=headers, type="lv2v",
                                             challenge=challenge, app=runner["app"], max_price=price)
            elif response.status < 400:
                lease = LiveRunnerSession(data["session_id"], data["app_url"], runner["url"], data["control_url"])
                if payment:
                    lease._start_payments(payment)
                return lease
            else:
                raise RuntimeError(f"Reservation failed HTTP {response.status}: {data}")
        paid = await payment.get_payment()
        request_headers.update({"Livepeer-Payment": paid.payment, "Livepeer-Segment": paid.seg_creds})
    raise RuntimeError("Reservation payment was rejected; no automatic paid retries")


async def _frames(images, app, prompt, mode, seconds, fps, timeout, signer, headers):
    lease = publisher = output = None
    received = asyncio.Queue(maxsize=16)
    results = []
    def on_frame(decoded):
        if decoded.kind == "video":
            if received.full():
                received.get_nowait()
            received.put_nowait(decoded.frame.to_ndarray(format="rgb24"))
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as http:
        try:
            runner = await _discover(http, app)
            lease = await _reserve_pixels(http, runner, signer, headers)
            base = _https(lease.app_url)
            channels = await _json(http, base + "/stream", {
                "prompt": prompt, "max_startup_s": timeout,
                "idle_timeout_s": max(10, seconds + 5),
                "metadata": {"environment": "test"},
            })
            output = MediaOutput(_https(channels["out"]), on_frame=on_frame)
            output.callback_tasks()
            publisher = MediaPublish(_https(channels["in"]), config=MediaPublishConfig(
                tracks=[VideoOutputConfig(fps=fps, keyframe_interval_s=0.25)],
                min_segment_wallclock_s=0.25))
            pts = 0
            async def send(image):
                nonlocal pts
                frame = av.VideoFrame.from_ndarray(image, format="rgb24")
                frame.pts, frame.time_base = pts, Fraction(1, fps)
                pts += 1
                await publisher.write_frame(frame)
            async def confirmed_frame(image):
                # Streaming workers emit fallback frames before inference is
                # ready. A decoded frame alone is not a successful model job.
                before = await _json(http, base + "/session")
                baseline = before.get("performance", {}).get("frames_processed", 0)
                deadline = asyncio.get_running_loop().time() + timeout
                last_status = before
                while asyncio.get_running_loop().time() < deadline:
                    await send(image)
                    await asyncio.sleep(1 / fps)
                    if pts % max(1, fps // 2) != 0:
                        continue
                    last_status = await _json(http, base + "/session")
                    performance = last_status.get("performance", {})
                    if performance.get("frames_processed", 0) > baseline and last_status.get("output") == "live":
                        # Discard earlier queued fallback frames and wait for
                        # a frame decoded after inference was confirmed.
                        while not received.empty():
                            received.get_nowait()
                        for _ in range(fps):
                            await send(image)
                            await asyncio.sleep(1 / fps)
                        return await asyncio.wait_for(received.get(), timeout=10)
                raise RuntimeError("No confirmed model inference before timeout: " + json.dumps(last_status))
            if mode == "batch":
                for image in images:
                    results.append(await confirmed_frame(image))
            else:
                # Warm up before starting the bounded clip clock; keep input
                # arriving while the encoder and model produce first output.
                results.append(await confirmed_frame(images[0]))
                for index in range(max(1, int(seconds * fps))):
                    await send(images[index % len(images)])
                    await asyncio.sleep(1 / fps)
                    while not received.empty():
                        results.append(received.get_nowait())
                    # Keep preview memory bounded during longer live clips.
                    results[:] = results[-120:]
            stats = await _json(http, base + "/stats")
            if not results:
                raise RuntimeError("Runner returned no images")
            # The server can produce fallback frames. Keep its counters in the
            # report so fallback output cannot be mistaken for proven inference.
            return results, {"app": app, "mode": mode, "frames_sent": pts,
                             "frames_received": len(results), "stats": stats}
        finally:
            if lease is not None:
                # Stop local funding before waiting for encoder shutdown.
                with contextlib.suppress(Exception):
                    await lease.stop_payments()
            for resource in (publisher, output):
                if resource is not None:
                    with contextlib.suppress(Exception):
                        await resource.close()
            if lease is not None:
                with contextlib.suppress(Exception):
                    await stop_runner_session(lease)


def _account(enabled):
    if not enabled:
        raise RuntimeError("Enable paid execution on this node to run the remote job")
    material = stream_signer()
    if material is None:
        raise RuntimeError("Sign in using the ComfyPeer account button")
    return material


def _tensor(frames):
    size = Image.fromarray(frames[0]).size
    return torch.from_numpy(np.stack([np.asarray(Image.fromarray(f).resize(size)).copy()
                                      for f in frames]).astype(np.float32) / 255.0)


class LivepeerRemoteFrames:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE",), "app": (APPS,), "mode": (["batch", "live"],),
            "prompt": ("STRING", {"multiline": True, "default": "Bioluminescent botanical architecture, cinematic light"}),
            "seconds": ("INT", {"default": 5, "min": 1, "max": 30}),
            "fps": ("INT", {"default": 8, "min": 1, "max": 30}),
            "timeout": ("INT", {"default": 60, "min": 5, "max": 180}),
            "paid_execution": ("BOOLEAN", {"default": False}),
        }}
    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("images", "usage_report")
    FUNCTION = "run"
    CATEGORY = "Livepeer/Remote API"

    async def run(self, images, app, mode, prompt, seconds, fps, timeout, paid_execution):
        signer, headers = _account(paid_execution)
        if len(images) > 32:
            raise ValueError("Use at most 32 input images per remote frame job")
        arrays = (images.detach().cpu().numpy()[:, :, :, :3].clip(0, 1) * 255).astype(np.uint8)
        arrays = np.stack([np.asarray(Image.fromarray(image).resize((512, 512))).copy()
                           for image in arrays])
        async def bounded():
            return await asyncio.wait_for(_frames(arrays, app, prompt, mode, seconds, fps, timeout,
                                                   signer, headers), timeout=timeout + seconds + 10)
        frames, report = await bounded()
        return _tensor(frames), json.dumps(report)


class LivepeerFalImageBatch:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "prompt": ("STRING", {"multiline": True, "default": "A floating botanical observatory above a neon ocean, wide cinematic composition"}),
            "count": ("INT", {"default": 2, "min": 1, "max": 4}),
            "seed": ("INT", {"default": 42, "min": 0, "max": 2147483647}),
            "paid_execution": ("BOOLEAN", {"default": False}),
        }}
    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("images", "receipts")
    FUNCTION = "run"
    CATEGORY = "Livepeer/Remote API"

    async def run(self, prompt, count, seed, paid_execution):
        signer, headers = _account(paid_execution)
        async def generate():
            frames, receipts = [], []
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180)) as http:
                for index in range(count):
                    cursor = await runner_selector(discovery_url=DISCOVERY,
                        app="comfystream/fal-flux-schnell", signer_url=signer, signer_headers=headers,
                        body={"prompt": prompt, "num_images": 1, "seed": seed + index,
                              "image_size": "square_hd"}, timeout=180)
                    try:
                        # Try one runner only: a paid provider operation is not
                        # safe to retry automatically after an ambiguous failure.
                        from livepeer_gateway.live_runner import call_runner
                        result = await call_runner(runner=cursor.candidates[0],
                            payload={"prompt": prompt, "num_images": 1, "seed": seed + index,
                                     "image_size": "square_hd"}, signer_url=signer,
                            signer_headers=headers, timeout=180)
                    finally:
                        await cursor.aclose()
                    receipt = result.data
                    payload = receipt.get("output", receipt)
                    if not payload.get("images"):
                        raise RuntimeError("FAL did not return completed image output: " + json.dumps(receipt))
                    for image in payload["images"]:
                        async with http.get(_https(image["url"])) as response:
                            response.raise_for_status()
                            frames.append(np.array(Image.open(io.BytesIO(await response.read())).convert("RGB")))
                    receipts.append(receipt)
            return _tensor(frames), json.dumps(receipts)
        return await generate()


NODE_CLASS_MAPPINGS = {"LivepeerRemoteFrames": LivepeerRemoteFrames,
                       "LivepeerFalImageBatch": LivepeerFalImageBatch}
NODE_DISPLAY_NAME_MAPPINGS = {"LivepeerRemoteFrames": "Livepeer · SD Turbo / Flux Klein Frames",
                              "LivepeerFalImageBatch": "Livepeer · FAL Image Batch"}
