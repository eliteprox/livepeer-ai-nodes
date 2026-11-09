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
from livepeer_gateway.errors import LivepeerGatewayError
from livepeer_gateway.live_runner import (
    LiveRunnerInstance, LiveRunnerPriceInfo, LiveRunnerSession, _runner_payment_type, call_runner,
    stop_runner_session,
)
from livepeer_gateway.media_output import MediaOutput
from livepeer_gateway.media_publish import MediaPublish, MediaPublishConfig, VideoOutputConfig
from livepeer_gateway.remote_signer import LivePaymentChallenge, LivePaymentSession, get_signer_info
from livepeer_gateway.selection import runner_selector

from .descriptions import build_request, describe_offerings, empty_catalog, find_choice
from .pymthouse_auth import PymtHouseAuthError
from .pymthouse_session import DISCOVERY_URL_OVERRIDE, OFFERINGS_URL, get_login_session, stream_signer

DISCOVERY = DISCOVERY_URL_OVERRIDE
OFFERINGS = OFFERINGS_URL
APPS = ["comfystream/sd-turbo", "comfystream/flux-klein"]


def discovered_apps(payload):
    """App ids advertised by discovery. Order is stable for the dropdown."""
    entries = payload
    if isinstance(payload, dict):
        entries = next((payload[key] for key in ("orchestrators", "data", "entries")
                        if isinstance(payload.get(key), list)), [])
    found = []
    seen = set()
    if isinstance(entries, list):
        for orch in entries:
            runners = orch.get("runners") if isinstance(orch, dict) else None
            if not isinstance(runners, list):
                continue
            for runner in runners:
                app = runner.get("app") if isinstance(runner, dict) else None
                if isinstance(app, str):
                    app = app.strip()
                if app and app not in seen:
                    seen.add(app)
                    found.append(app)
    found.sort()
    return found


async def fetch_catalog():
    """One choice per offering, with forms from discovery metadata and the runner pool."""
    try:
        _url, headers = await get_login_session().discovery_request()
    except PymtHouseAuthError as exc:
        return empty_catalog(exc.message)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as http:
            offerings = await _get_json(http, OFFERINGS)
            discovery = await _get_json(http, DISCOVERY, headers)
    except Exception:
        return empty_catalog("Could not read offerings.")
    try:
        return describe_offerings(offerings, discovery)
    except Exception as exc:
        return empty_catalog(str(exc))


def register_discovery_routes():
    if getattr(register_discovery_routes, "_registered", False):
        return
    try:
        from aiohttp import web
        from server import PromptServer
    except ImportError:
        return
    routes = getattr(getattr(PromptServer, "instance", None), "routes", None)
    if routes is None:
        return

    @routes.get("/livepeer-ai-nodes/discovery/apps")
    @routes.get("/livepeer-ai-nodes/discovery/capabilities")
    async def discovery_apps(_request):
        return web.json_response(await fetch_catalog())

    register_discovery_routes._registered = True


def _https(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Runner endpoints must use HTTPS")
    return url.rstrip("/")


async def _get_json(http, url, headers=None):
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("Discovery URL must be an http(s) URL")
    async with http.request("GET", url, headers=headers) as response:
        data = await response.json(content_type=None)
        if response.status >= 400:
            raise RuntimeError(f"Remote HTTP {response.status}: {data}")
        return data


async def _json(http, url, body=None, headers=None):
    async with http.request("POST" if body is not None else "GET", _https(url),
                            json=body, headers=headers) as response:
        data = await response.json(content_type=None)
        if response.status >= 400:
            raise RuntimeError(f"Remote HTTP {response.status}: {data}")
        return data


async def _discover(http, app, discovery, headers):
    payload = await _get_json(http, discovery, headers)
    entries = payload
    if isinstance(payload, dict):
        entries = next((payload[key] for key in ("orchestrators", "data", "entries")
                        if isinstance(payload.get(key), list)), [])
    if not isinstance(entries, list):
        entries = []
    candidates = [r for orch in entries if isinstance(orch, dict)
                  for r in orch.get("runners") or []
                  if isinstance(r, dict) and r.get("app") == app and r.get("capacity_available", 0) > 0]
    if not candidates:
        raise RuntimeError(f"No available capacity for {app}")
    return candidates[0]


def _payment_type(price):
    """Use the gateway's unit map. Seconds and hours are `live`; fixed is upfront."""
    unit = str((price or {}).get("unit") or "").strip().lower()
    try:
        return _runner_payment_type(None, unit or None)
    except LivepeerGatewayError as exc:
        raise RuntimeError(str(exc)) from exc


async def _reserve_pixels(http, runner, signer, headers):
    """Pay with the type that matches the runner's advertised unit."""
    price = runner.get("price_info") or {}
    payment_type = _payment_type(price)
    material = await get_signer_info(signer, frozenset(headers.items()))
    request_headers = {"Livepeer-Payer-Address": material.address}
    payment = None
    for _ in range(2):
        async with http.post(_https(runner["url"]), json={}, headers=request_headers) as response:
            data = await response.json(content_type=None)
            if response.status == 402 and payment is None:
                challenge = LivePaymentChallenge(data["payment_params"], data["manifest_id"], data["payment_url"])
                payment = LivePaymentSession(signer, signer_headers=headers, type=payment_type,
                                             challenge=challenge, app=runner["app"], max_price=price)
            elif response.status < 400:
                lease = LiveRunnerSession(data["session_id"], data["app_url"], runner["url"], data["control_url"])
                if payment and payment_type == "live":
                    lease._start_payments(payment)
                return lease
            else:
                raise RuntimeError(f"Reservation failed HTTP {response.status}: {data}")
        paid = await payment.get_payment()
        request_headers.update({"Livepeer-Payment": paid.payment, "Livepeer-Segment": paid.seg_creds})
    raise RuntimeError("Reservation payment was rejected; no automatic paid retries")


async def _frames(images, app, prompt, mode, seconds, fps, timeout, signer, headers, discovery, channels=None):
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
            if channels is None:
                runner = await _discover(http, app, discovery, headers)
                lease = await _reserve_pixels(http, runner, signer, headers)
                base = _https(lease.app_url)
                channels = await _json(http, base + "/stream", {
                    "prompt": prompt, "max_startup_s": timeout,
                    "idle_timeout_s": max(10, seconds + 5),
                    "metadata": {"environment": "test"},
                })
            else:
                base = None
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
                # A described session already returned its channel URLs. Collect
                # decoded frames from those channels; there is no second lease.
                if base is None:
                    deadline = asyncio.get_running_loop().time() + timeout
                    while asyncio.get_running_loop().time() < deadline:
                        await send(image)
                        await asyncio.sleep(1 / fps)
                        if not received.empty():
                            return received.get_nowait()
                    raise RuntimeError("Runner returned no frames before timeout")
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
            stats = await _json(http, base + "/stats") if base else {}
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


def _blank():
    return torch.zeros((1, 64, 64, 3))


def _join(base, path):
    if not path or path == "/":
        return base.rstrip("/")
    return base.rstrip("/") + "/" + path.lstrip("/")


def _ready_runner(choice):
    runners = [item for item in choice.get("runners") or [] if (item.get("capacity_available") or 0) > 0]
    if not runners:
        raise RuntimeError(f"No available capacity for {choice['label']}")
    return runners[0]


def _instance(record):
    price = record.get("price") or {}
    info = None
    if price.get("unit"):
        info = LiveRunnerPriceInfo(price.get("price") or 0, price.get("currency") or "wei", price["unit"])
    return LiveRunnerInstance(
        url=record["url"], app=record.get("app") or "", runner_id=record.get("runner_id") or "",
        mode=record.get("mode") or "", orchestrator_url=record.get("orchestrator") or "",
        raw=record, price_info=info,
    )


def _channel_urls(data, call):
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("in"), str) and isinstance(data.get("out"), str):
        return data["in"], data["out"]
    channels = data.get("channels") if isinstance(data.get("channels"), dict) else None
    if channels is None:
        return None
    spec = (call or {}).get("channels") or {}
    incoming = (spec.get("in") or ["in"])[0]
    outgoing = (spec.get("out") or ["out"])[0]
    if incoming != outgoing and isinstance(channels.get(incoming), str) and isinstance(channels.get(outgoing), str):
        return channels[incoming], channels[outgoing]
    if isinstance(channels.get("in"), str) and isinstance(channels.get("out"), str):
        return channels["in"], channels["out"]
    return None


def _decode_images(data):
    frames = []
    if not isinstance(data, dict):
        return frames
    for item in data.get("data") or []:
        if isinstance(item, dict) and isinstance(item.get("b64_json"), str):
            frames.append(item["b64_json"])
    return frames


def _report(data):
    if not isinstance(data, dict):
        return data

    def trim(value):
        if isinstance(value, str) and len(value) > 240:
            return f"<{len(value)} chars>"
        if isinstance(value, dict):
            return {key: trim(item) for key, item in value.items()}
        if isinstance(value, list):
            return [trim(item) for item in value[:20]]
        return value

    return trim(data)


class LivepeerRemoteFrames:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE",),
            "app": ("STRING", {
                "default": "",
                "tooltip": "Capability from discovery. Pick a listed choice or type an app id.",
            }),
            "mode": (["batch", "live"],),
            "prompt": ("STRING", {"multiline": True, "default": "Bioluminescent botanical architecture, cinematic light"}),
            "seconds": ("INT", {"default": 5, "min": 1, "max": 30}),
            "fps": ("INT", {"default": 8, "min": 1, "max": 30}),
            "timeout": ("INT", {"default": 60, "min": 5, "max": 180}),
            "paid_execution": ("BOOLEAN", {"default": False}),
        }, "optional": {
            "fields": ("STRING", {"default": "{}", "multiline": True,
                                  "tooltip": "Request fields for the selected capability. The form fills this in."}),
        }}
    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("images", "usage_report")
    FUNCTION = "run"
    CATEGORY = "Livepeer/Remote API"

    async def run(self, images, app, mode, prompt, seconds, fps, timeout, paid_execution, fields="{}"):
        app = app.strip() if isinstance(app, str) else ""
        if not app:
            raise ValueError("Pick a discovered capability")
        try:
            values = json.loads(fields) if isinstance(fields, str) and fields.strip() else {}
        except ValueError as exc:
            raise ValueError("Capability fields are not valid JSON") from exc
        if not isinstance(values, dict):
            raise ValueError("Capability fields must be a JSON object")
        signer, headers = await asyncio.to_thread(_account, paid_execution)
        catalog = await fetch_catalog()
        if catalog.get("message") and not catalog.get("choices"):
            raise RuntimeError(catalog["message"])
        choice = find_choice(catalog, app)
        if choice is None:
            limit = 64 if mode == "live" else 32
            if images is None or not 1 <= len(images) <= limit:
                raise ValueError(f"Use between 1 and {limit} input images for {mode} execution")
            arrays = (images.detach().cpu().numpy()[:, :, :, :3].clip(0, 1) * 255).astype(np.uint8)
            arrays = np.stack([np.asarray(Image.fromarray(image).resize((512, 512))).copy() for image in arrays])

            async def legacy():
                return await asyncio.wait_for(_frames(arrays, app, prompt, mode, seconds, fps, timeout,
                                                       signer, headers, DISCOVERY), timeout=timeout + seconds + 10)
            frames, report = await legacy()
            return _tensor(frames), json.dumps(report)

        if not choice.get("call"):
            raise RuntimeError(choice.get("message") or f"No call description for {choice['label']}")
        content_type = choice["call"].get("content_type")
        if content_type and "json" not in content_type:
            raise RuntimeError(f"{choice['label']} is called as {content_type}")
        body = build_request(choice, values, prompt=prompt, seconds=seconds, timeout=timeout)
        record = _ready_runner(choice)
        result = await call_runner(
            runner=_instance(record), runner_url=_join(record["url"], choice["call"]["path"]),
            payload=body, method=choice["call"]["method"], signer_url=signer, signer_headers=headers,
            timeout=timeout,
        )
        data = result.data if isinstance(result.data, dict) else {"body": result.data}
        urls = _channel_urls(data, choice.get("call"))
        if urls and images is not None and len(images) > 0:
            arrays = (images.detach().cpu().numpy()[:, :, :, :3].clip(0, 1) * 255).astype(np.uint8)
            width = int(values.get("width") or 512)
            height = int(values.get("height") or 512)
            arrays = np.stack([np.asarray(Image.fromarray(image).resize((width, height))).copy() for image in arrays])
            frames, report = await _frames(arrays, record["app"], prompt, mode, seconds, fps, timeout,
                                            signer, headers, DISCOVERY, channels={"in": urls[0], "out": urls[1]})
            report["call"] = choice["call"]
            return _tensor(frames), json.dumps(report)
        encoded = _decode_images(data)
        if encoded:
            import base64
            frames = [np.array(Image.open(io.BytesIO(base64.b64decode(item))).convert("RGB")) for item in encoded]
            return _tensor(frames), json.dumps({"call": choice["call"], "answer": _report(data)})
        return _blank(), json.dumps({"call": choice["call"], "answer": _report(data)})


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


register_discovery_routes()

NODE_CLASS_MAPPINGS = {"LivepeerRemoteFrames": LivepeerRemoteFrames,
                       "LivepeerFalImageBatch": LivepeerFalImageBatch}
NODE_DISPLAY_NAME_MAPPINGS = {"LivepeerRemoteFrames": "Livepeer · SD Turbo / Flux Klein Frames",
                              "LivepeerFalImageBatch": "Livepeer · FAL Image Batch"}
