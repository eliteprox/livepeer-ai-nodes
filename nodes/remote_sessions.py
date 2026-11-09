"""Persistent, isolated ComfyStream media sessions across graph executions."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import threading
import time
import uuid
from fractions import Fraction

import av
import numpy as np
import torch
from livepeer_gateway import AudioOutputConfig, MediaOutput, MediaPublish, MediaPublishConfig, VideoOutputConfig
from livepeer_gateway.selection import reserve_session
from livepeer_gateway.live_runner import stop_runner_session

from .pymthouse_session import stream_signer

DEFAULT_WORKFLOW = json.dumps({
    "1": {"class_type": "LoadTensor", "inputs": {}},
    "2": {"class_type": "SaveTensor", "inputs": {"images": ["1", 0]}},
})


def workflow_json(value):
    value = json.loads(value)
    if not isinstance(value, dict) or not value or not all(
        isinstance(node, dict) and isinstance(node.get("class_type"), str) for node in value.values()
    ):
        raise ValueError("Supply a ComfyUI API-format workflow JSON object")
    return value


class Session:
    def __init__(self, lease, http, config):
        self.lease, self.http, self.config = lease, http, config
        self.publisher = self.output = None
        self.ws = self.reader = None
        self.lock = asyncio.Lock()
        self.last_used = self.opened = time.monotonic()
        self.closed = False
        self.error = ""
        self.video_pts = self.audio_pts = 0
        self.sequence = 0
        self.video_offset = None
        self.pending = {}
        self.latest = None
        self.audio = []
        self.audio_samples = 0
        self.audio_rate = 48000
        self.received = 0
        self.expiry_task = None
        self.channels = {}
        self.recorders = []

    async def post(self, path, body):
        async with self.http.post(self.lease.app_url.rstrip("/") + path, json=body) as response:
            data = await response.json(content_type=None)
            if response.status >= 400:
                raise RuntimeError(f"Remote {path} HTTP {response.status}: {data}")
            return data

    def decoded(self, decoded):
        if self.closed:
            return
        frame = decoded.frame
        if decoded.kind == "video":
            self.received += 1
            self.latest = frame.to_ndarray(format="rgb24")
            for recorder in self.recorders:
                recorder.capture(self.latest, self.received, self.config['revision'])
            if not self.pending:
                return
            if frame.pts is None or frame.time_base is None:
                self.fail("Ordered output requires media timestamps")
                return
            timestamp = Fraction(frame.pts) * frame.time_base
            if self.video_offset is None:
                # Pipeline preserves PTS; MPEG-TS remuxing may add a constant offset.
                self.video_offset = timestamp - next(iter(self.pending))
            key = timestamp - self.video_offset
            future = self.pending.pop(key, None)
            if future is None:
                self.fail("Output timestamp does not match an outstanding image; workflow must preserve one output per input")
            elif not future.done():
                future.set_result(self.latest.copy())
        elif decoded.kind == "audio":
            samples = frame.to_ndarray()
            if not frame.format.is_planar:
                samples = samples.reshape(-1, len(frame.layout.channels)).T
            if samples.dtype.kind in "iu":
                samples = samples.astype(np.float32) / max(abs(np.iinfo(samples.dtype).min), np.iinfo(samples.dtype).max)
            else:
                samples = samples.astype(np.float32)
            self.audio_rate = frame.sample_rate
            self.audio.append(samples.copy())
            self.audio_samples += samples.shape[-1]
            while self.audio and self.audio_samples > self.audio_rate * 60:
                self.audio_samples -= self.audio.pop(0).shape[-1]

    def fail(self, message):
        self.error = message
        for future in self.pending.values():
            if not future.done():
                future.set_exception(RuntimeError(message))
        self.pending.clear()

    async def close(self):
        if self.closed:
            return
        self.closed = True
        for recorder in self.recorders:
            recorder.stopped = True
        self.fail("Remote session closed")
        if self.expiry_task and self.expiry_task is not asyncio.current_task():
            self.expiry_task.cancel()
        try:
            await self.lease.stop_payments()
        finally:
            if self.reader and self.reader is not asyncio.current_task():
                self.reader.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.reader
            if self.ws:
                with contextlib.suppress(Exception):
                    await self.ws.close()
            for resource in (self.publisher, self.output):
                if resource:
                    with contextlib.suppress(Exception):
                        await resource.close()
            try:
                await stop_runner_session(self.lease)
            finally:
                await self.http.close()

    async def expire(self):
        while not self.closed:
            await asyncio.sleep(1)
            now = time.monotonic()
            payment_task = getattr(self.lease, "_payment_task", None)
            if payment_task is not None and payment_task.done():
                self.error = "Remote payment task ended; reopen after checking account status"
                await self.close()
                return
            if now - self.last_used >= self.config["idle_seconds"] or now - self.opened >= self.config["max_seconds"]:
                await self.close()

    def check(self):
        if self.closed or self.error:
            raise RuntimeError(self.error or "Session expired; explicitly reopen it")
        self.last_used = time.monotonic()

    async def images(self, images, timeout, delivery="ordered"):
        async with self.lock:
            self.check()
            if not self.channels["modalities"]["video"]["input"]:
                raise RuntimeError("Remote workflow has no image input")
            results, manifest = [], []
            if self.ws is not None:
                # The runner's JPEG path emits one response for each queued
                # input in FIFO order. Keep all requests in this batch pending
                # before waiting; an arbitrary workflow must preserve 1:1 I/O.
                from PIL import Image
                futures = []
                received_before = self.received
                next_send = asyncio.get_running_loop().time()
                try:
                    for image in images:
                        # The live runner drops queued inputs when ingest outruns
                        # processing. Feed a real frame clock instead of a burst.
                        delay = next_send - asyncio.get_running_loop().time()
                        if delay > 0:
                            await asyncio.sleep(delay)
                        self.check()
                        key = Fraction(self.video_pts, self.config["fps"])
                        future = asyncio.get_running_loop().create_future()
                        self.pending[key] = future
                        futures.append(future)
                        self.sequence += 1
                        manifest.append({"sequence": self.sequence, "input_pts": self.video_pts,
                                         "workflow_revision": self.config["revision"], "correlation": "serialized-fifo"})
                        self.video_pts += 1
                        buffer = io.BytesIO()
                        Image.fromarray(image).save(buffer, format="JPEG", quality=95)
                        await self.ws.send_bytes(buffer.getvalue())
                        next_send = asyncio.get_running_loop().time() + 1 / self.config["fps"]
                        if delivery == "ordered":
                            # One outstanding request prevents the live runner's
                            # single-slot input queue from discarding batch items.
                            await asyncio.wait_for(asyncio.shield(future), timeout)
                    return await asyncio.wait_for(asyncio.gather(*futures), timeout), manifest
                except asyncio.TimeoutError as exc:
                    received = self.received - received_before
                    await self.close()
                    raise RuntimeError(
                        f"Remote frame batch timed out: received {received}/{len(futures)} frames "
                        f"in {delivery} mode. Model startup or inference may exceed the timeout; "
                        "ordered mode requires one output per input."
                    ) from exc
                except BaseException:
                    await self.close()
                    raise
            # Serialize exchanges: no overlapping graph execution can reorder inputs.
            try:
                for image in images:
                    self.check()
                    pts = self.video_pts
                    self.video_pts += 1
                    self.sequence += 1
                    key = Fraction(pts, self.config["fps"])
                    future = asyncio.get_running_loop().create_future()
                    self.pending[key] = future
                    frame = av.VideoFrame.from_ndarray(image, format="rgb24")
                    frame.pts, frame.time_base = pts, Fraction(1, self.config["fps"])
                    await self.publisher.write_frame(frame)
                    results.append(await asyncio.wait_for(future, timeout))
                    manifest.append({"sequence": self.sequence, "input_pts": pts,
                                     "time_base": str(frame.time_base), "workflow_revision": self.config["revision"]})
            except BaseException:
                await self.close()
                raise
            return results, manifest

    async def receive_jpegs(self):
        import aiohttp
        from PIL import Image
        try:
            async for message in self.ws:
                if message.type == aiohttp.WSMsgType.BINARY:
                    image = np.asarray(Image.open(io.BytesIO(message.data)).convert("RGB")).copy()
                    self.latest, self.received = image, self.received + 1
                    if not self.pending:
                        self.fail("Unsolicited image output; workflow must preserve one output per input")
                        break
                    for recorder in self.recorders:
                        recorder.capture(image, self.received, self.config['revision'])
                    future = self.pending.pop(next(iter(self.pending)))
                    if not future.done():
                        future.set_result(image)
                elif message.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(message.data)
                    if data.get("type") == "error":
                        raise RuntimeError(str(data.get("error")))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.fail(str(exc))
        finally:
            await self.close()


class Runtime:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True, name="remote-comfy-sessions")
        self.thread.start()
        self.sessions = {}

    async def call(self, coroutine):
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coroutine, self.loop))

    def get(self, handle):
        session = self.sessions.get(handle.get("id")) if isinstance(handle, dict) else None
        if session is None:
            raise RuntimeError("Unknown session handle; reopen after a server restart")
        session.check()
        return session

    async def open(self, config, signer, headers):
        import aiohttp
        self.sessions = {key: value for key, value in self.sessions.items() if not value.closed}
        if len(self.sessions) >= 16:
            raise RuntimeError("Close an existing remote session before opening another")
        lease = await reserve_session(orchestrators=[config["orchestrator"]], app="comfystream",
                                      signer_url=signer, signer_headers=headers, timeout=20)
        session = Session(lease, aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)), config)
        try:
            if not config["audio"]:
                import aiohttp
                from urllib.parse import urlparse
                parsed = urlparse(lease.app_url.rstrip("/") + "/ws_stream")
                session.ws = await session.http.ws_connect(parsed._replace(scheme="wss").geturl())
                await session.ws.send_json({"type": "start", "prompts": config["workflow"],
                                            "width": config["width"], "height": config["height"]})
                ready = await session.ws.receive_json(timeout=20)
                if ready.get("type") != "ready":
                    raise RuntimeError(f"Remote workflow did not become ready: {ready}")
                session.channels = ready
                session.reader = asyncio.create_task(session.receive_jpegs())
                session.expiry_task = asyncio.create_task(session.expire())
                handle = {"id": str(uuid.uuid4())}
                self.sessions[handle["id"]] = session
                return handle
            session.channels = await session.post("/start_stream", {
                "prompts": config["workflow"], "width": config["width"], "height": config["height"],
                "audio": config["audio"],
            })
            io = session.channels["modalities"]
            tracks = []
            if io["video"]["input"]:
                tracks.append(VideoOutputConfig(fps=config["fps"], keyframe_interval_s=0.1))
            if io["audio"]["input"] and config["audio"]:
                tracks.append(AudioOutputConfig(sample_rate=48000, layout="stereo"))
            if not tracks or not session.channels.get("out"):
                raise RuntimeError("Workflow must expose media inputs and outputs")
            session.publisher = MediaPublish(session.channels["in"], config=MediaPublishConfig(
                tracks=tracks, min_segment_wallclock_s=0.1, segment_post_idle_timeout_s=0.3,
                track_wait_timeout_s=0.5))
            session.output = MediaOutput(session.channels["out"], on_frame=session.decoded)
            for task in session.output.callback_tasks():
                task.add_done_callback(lambda _task: asyncio.create_task(session.close()) if not session.closed else None)
            session.expiry_task = asyncio.create_task(session.expire())
            handle = {"id": str(uuid.uuid4())}
            self.sessions[handle["id"]] = session
            return handle
        except BaseException:
            await session.close()
            raise


_runtime = None
_runtime_lock = threading.Lock()


def runtime():
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = Runtime()
    return _runtime


class OpenRemoteSession:
    def __init__(self):
        self.handle = None
        self.fingerprint = None

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "workflow_json": ("STRING", {"default": DEFAULT_WORKFLOW, "multiline": True}),
            "orchestrator": ("STRING", {"default": "https://ai1.eliteencoder.net:8936"}),
            "width": ("INT", {"default": 512, "min": 64, "max": 2048}),
            "height": ("INT", {"default": 512, "min": 64, "max": 2048}),
            "fps": ("INT", {"default": 8, "min": 1, "max": 60}),
            "audio": ("BOOLEAN", {"default": False}),
            "idle_seconds": ("INT", {"default": 30, "min": 5, "max": 600}),
            "max_seconds": ("INT", {"default": 120, "min": 5, "max": 240}),
            "paid_execution": ("BOOLEAN", {"default": False}),
            "reopen": ("BOOLEAN", {"default": False}),
        }}
    RETURN_TYPES = ("REMOTE_SESSION",)
    FUNCTION = "open"
    CATEGORY = "Livepeer/Remote Session"
    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    async def open(self, workflow_json, orchestrator, width, height, fps, audio, idle_seconds, max_seconds, paid_execution, reopen=False):
        if reopen and self.handle:
            await CloseRemoteSession().close(self.handle)
            self.handle = None
        if not paid_execution:
            if self.handle:
                await CloseRemoteSession().close(self.handle)
            self.handle = None
            raise RuntimeError("Enable paid execution to open a remote session")
        from .stream.network_controller import _require_https_orchestrator
        config = dict(workflow=globals()["workflow_json"](workflow_json), orchestrator=_require_https_orchestrator(orchestrator),
                      width=width, height=height, fps=fps, audio=audio,
                      idle_seconds=idle_seconds, max_seconds=max_seconds, revision=0)
        fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        rt = runtime()
        if self.handle:
            async def reuse():
                session = rt.get(self.handle)
                if fingerprint != self.fingerprint:
                    raise RuntimeError("Open settings changed; close/reopen or use Update Remote Workflow")
            await rt.call(reuse())
            return (self.handle,)
        signer = await asyncio.to_thread(stream_signer)
        if not signer:
            raise RuntimeError("Sign in using the ComfyPeer account button")
        self.handle = await rt.call(rt.open(config, *signer))
        self.fingerprint = fingerprint
        return (self.handle,)


class RemoteImageIO:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"session": ("REMOTE_SESSION",), "images": ("IMAGE",),
                             "timeout": ("INT", {"default": 120, "min": 1, "max": 120})},
                "optional": {"delivery": (["ordered", "paced"], {"default": "ordered"})}}
    RETURN_TYPES = ("REMOTE_SESSION", "IMAGE", "STRING")
    RETURN_NAMES = ("session", "images", "ordered_results")
    FUNCTION = "exchange"
    CATEGORY = "Livepeer/Remote Session"
    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")
    async def exchange(self, session, images, timeout=120, delivery="ordered"):
        if len(images) < 1 or len(images) > 64:
            raise ValueError("Send between 1 and 64 images per execution")
        arrays = (images.detach().cpu().numpy()[..., :3].clip(0, 1) * 255).astype(np.uint8)
        rt = runtime()
        async def send():
            return await rt.get(session).images(arrays, timeout, delivery)
        frames, manifest = await rt.call(send())
        return session, torch.from_numpy(np.stack(frames).astype(np.float32) / 255), json.dumps(manifest)


class UpdateRemoteWorkflow:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"session": ("REMOTE_SESSION",),
                             "workflow_json": ("STRING", {"multiline": True, "default": DEFAULT_WORKFLOW})}}
    RETURN_TYPES = ("REMOTE_SESSION",)
    FUNCTION = "update"
    CATEGORY = "Livepeer/Remote Session"
    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")
    async def update(self, session, workflow_json):
        workflow = globals()["workflow_json"](workflow_json)
        rt = runtime()
        async def apply():
            s = rt.get(session)
            async with s.lock:
                if workflow == s.config["workflow"]:
                    return
                response = await s.post("/update_stream", {"prompts": workflow})
                if response.get("modalities") != s.channels.get("modalities"):
                    await s.close()
                    raise RuntimeError("I/O modalities changed; reopen the session to negotiate new tracks")
                s.config["workflow"] = workflow
                s.config["revision"] += 1
                s.video_offset = None
                s.latest, s.audio = None, []
                s.audio_samples = 0
        await rt.call(apply())
        return (session,)


class CloseRemoteSession:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"session": ("REMOTE_SESSION",)}}
    RETURN_TYPES = ("STRING",)
    OUTPUT_NODE = True
    FUNCTION = "close"
    CATEGORY = "Livepeer/Remote Session"
    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")
    async def close(self, session):
        rt = runtime()
        async def release():
            s = rt.sessions.get(session.get("id"))
            if s:
                await s.close()
        await rt.call(release())
        return {"ui": {"text": ["Remote session closed"]}, "result": ("closed",)}


class RemoteAudioInput:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"session": ("REMOTE_SESSION",), "audio": ("AUDIO",)}}
    RETURN_TYPES = ("REMOTE_SESSION",)
    FUNCTION = "send"
    CATEGORY = "Livepeer/Remote Session"
    IS_CHANGED = classmethod(lambda cls, **kwargs: float("nan"))
    async def send(self, session, audio):
        samples = audio["waveform"].detach().cpu().float()
        rate = int(audio["sample_rate"])
        if samples.ndim != 3 or samples.shape[0] != 1 or samples.shape[1] not in (1, 2) or rate <= 0:
            raise ValueError("Expected one mono/stereo AUDIO waveform [1, channels, samples]")
        if samples.shape[-1] > rate * 60:
            raise ValueError("Send at most 60 seconds per execution")
        rt = runtime()
        async def push():
            s = rt.get(session)
            async with s.lock:
                if not s.config["audio"] or not s.channels["modalities"]["audio"]["input"]:
                    raise RuntimeError("Session needs audio enabled and an audio-input workflow")
                resampler = av.AudioResampler(format="fltp", layout="stereo", rate=48000)
                try:
                    for start in range(0, samples.shape[-1], max(1, rate // 50)):
                        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(samples[0, :, start:start + max(1, rate // 50)].numpy()),
                            format="fltp", layout="mono" if samples.shape[1] == 1 else "stereo")
                        frame.sample_rate = rate
                        for converted in resampler.resample(frame):
                            converted.pts, converted.time_base = s.audio_pts, Fraction(1, 48000)
                            s.audio_pts += converted.samples
                            await s.publisher.write_frame(converted)
                    for converted in resampler.resample(None):
                        converted.pts, converted.time_base = s.audio_pts, Fraction(1, 48000)
                        s.audio_pts += converted.samples
                        await s.publisher.write_frame(converted)
                except BaseException:
                    await s.close()
                    raise
        await rt.call(push())
        return (session,)


class RemoteAudioOutput:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"session": ("REMOTE_SESSION",), "timeout": ("INT", {"default": 15, "min": 1, "max": 120})}}
    RETURN_TYPES = ("REMOTE_SESSION", "AUDIO")
    FUNCTION = "receive"
    CATEGORY = "Livepeer/Remote Session"
    IS_CHANGED = classmethod(lambda cls, **kwargs: float("nan"))
    async def receive(self, session, timeout=15):
        rt = runtime()
        async def pull():
            s = rt.get(session)
            if not s.channels["modalities"]["audio"]["output"]:
                raise RuntimeError("Workflow has no audio output")
            deadline = time.monotonic() + timeout
            while not s.audio:
                if time.monotonic() >= deadline:
                    raise TimeoutError("No remote audio received")
                s.check()
                await asyncio.sleep(0.05)
            async with s.lock:
                data = np.concatenate(s.audio, axis=1)
                s.audio, s.audio_samples = [], 0
                return {"waveform": torch.from_numpy(data).unsqueeze(0), "sample_rate": s.audio_rate}
        return session, await rt.call(pull())


class RemoteVideoIO:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"session": ("REMOTE_SESSION",), "video": ("VIDEO",),
                             "timeout": ("INT", {"default": 30, "min": 1, "max": 120})}}
    RETURN_TYPES = ("REMOTE_SESSION", "IMAGE", "STRING")
    RETURN_NAMES = ("session", "frames", "ordered_results")
    FUNCTION = "exchange"
    CATEGORY = "Livepeer/Remote Session"
    IS_CHANGED = classmethod(lambda cls, **kwargs: float("nan"))
    async def exchange(self, session, video, timeout=30):
        components = await asyncio.to_thread(video.get_components)
        if len(components.images) > 64:
            raise ValueError("Split video into chunks of at most 64 frames")
        rt = runtime()
        async def validate_rate():
            if Fraction(components.frame_rate) != Fraction(rt.get(session).config["fps"]):
                raise ValueError("Video frame rate must match the session FPS; resample it before sending")
        await rt.call(validate_rate())
        if components.audio is not None:
            await RemoteAudioInput().send(session, components.audio)
        return await RemoteImageIO().exchange(session, components.images, timeout)


class RemoteImageOutput:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"session": ("REMOTE_SESSION",)}}
    RETURN_TYPES = ("IMAGE", "STRING")
    FUNCTION = "receive"
    CATEGORY = "Livepeer/Remote Session"
    IS_CHANGED = classmethod(lambda cls, **kwargs: float("nan"))
    async def receive(self, session):
        rt = runtime()
        async def pull():
            s = rt.get(session)
            if s.latest is None:
                raise RuntimeError("No remote image yet; execute an input node first")
            return s.latest.copy(), {"received": s.received, "revision": s.config["revision"], "session_id": s.lease.session_id}
        frame, status = await rt.call(pull())
        return torch.from_numpy(frame.astype(np.float32) / 255).unsqueeze(0), json.dumps(status)


class AssembleRemoteVideo:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"images": ("IMAGE",), "fps": ("INT", {"default": 8, "min": 1, "max": 60})},
                "optional": {"audio": ("AUDIO",)}}
    RETURN_TYPES = ("VIDEO",)
    FUNCTION = "assemble"
    CATEGORY = "Livepeer/Remote Session"
    def assemble(self, images, fps, audio=None):
        from comfy_api.latest._input_impl import VideoFromComponents
        from comfy_api.latest._util.video_types import VideoComponents
        return (VideoFromComponents(VideoComponents(images=images, frame_rate=Fraction(fps), audio=audio)),)


NODE_CLASS_MAPPINGS = {cls.__name__: cls for cls in (
    OpenRemoteSession, RemoteImageIO, RemoteImageOutput, RemoteVideoIO,
    RemoteAudioInput, RemoteAudioOutput, AssembleRemoteVideo, UpdateRemoteWorkflow, CloseRemoteSession)}
NODE_DISPLAY_NAME_MAPPINGS = {name: name for name in NODE_CLASS_MAPPINGS}
