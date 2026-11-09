"""Bounded session capture with live previews and explicit export snapshots."""
import asyncio
import base64
from fractions import Fraction
import io
import json
from pathlib import Path
import re
import time
import uuid

import av
import numpy as np
from PIL import Image
import torch

MAX_BYTES = 256 * 1024 * 1024


class Recording:
    def __init__(self, mode, every_n, max_frames, fps, preview=None):
        self.mode, self.every_n, self.max_frames = mode, every_n, max_frames
        self.fps, self.preview = fps, preview
        self.frames, self.entries = [], []
        self.latest = None
        self.seen = self.bytes = self.omitted = 0
        self.stopped = False
        self.last_preview = 0
        self.preview_error = ''
        self.id = uuid.uuid4().hex

    def capture(self, image, sequence, revision):
        if self.stopped:
            return
        self.seen += 1
        self.latest = image.copy()
        selected = self.mode != 'every_nth' or (self.seen - 1) % self.every_n == 0
        if selected:
            if self.mode == 'latest':
                self.frames.clear(); self.entries.clear(); self.bytes = 0
            if len(self.frames) >= self.max_frames or self.bytes + image.nbytes > MAX_BYTES:
                self.omitted += 1
            else:
                self.frames.append(image.copy())
                self.bytes += image.nbytes
                self.entries.append(dict(received_sequence=sequence, workflow_revision=revision,
                                         capture_index=self.seen))
        now = time.monotonic()
        if self.preview and now - self.last_preview >= .25:
            self.last_preview = now
            try:
                self.preview(self.latest, dict(seen=self.seen, captured=len(self.frames), omitted=self.omitted))
            except Exception as exc:
                self.preview_error = str(exc)

    def snapshot(self):
        if not self.frames:
            raise RuntimeError('No captured frames yet; connect the session output of Remote Image I/O to this node')
        return list(self.frames), self.latest.copy(), dict(
            recording_id=self.id, mode=self.mode, seen=self.seen, captured=len(self.frames),
            capture_limit_reached=self.omitted > 0, omitted=self.omitted, stopped=self.stopped,
            preview_error=self.preview_error,
            sample_fps=self.fps / (self.every_n if self.mode == 'every_nth' else 1),
            frames=list(self.entries))


def export(frames, latest, report, save, prefix, output_dir):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', prefix):
        raise ValueError('Filename prefix must contain only letters, numbers, underscores or hyphens')
    size = Image.fromarray(frames[0]).size
    normalized = [np.asarray(Image.fromarray(frame).resize(size)).copy() for frame in frames]
    report['samples_resized_to'] = list(size)
    report['files'] = []
    if save != 'none':
        folder = Path(output_dir) / 'livepeer-recordings' / f'{prefix}_{uuid.uuid4().hex[:12]}'
        folder.mkdir(parents=True, exist_ok=False)
        if save in ('images', 'images_and_video'):
            for index, frame in enumerate(frames):
                path = folder / f'frame_{index:06d}.png'
                Image.fromarray(frame).save(path)
                report['files'].append(str(path))
        if save in ('video', 'images_and_video'):
            path = folder / 'recording.mp4'
            # yuv420p requires even dimensions; samples retain original shape.
            video_size = (size[0] + size[0] % 2, size[1] + size[1] % 2)
            with av.open(str(path), 'w') as container:
                stream = container.add_stream('libx264', rate=Fraction(str(report['sample_fps'])).limit_denominator(1000))
                stream.width, stream.height = video_size
                stream.pix_fmt = 'yuv420p'
                for index, frame in enumerate(normalized):
                    frame = av.VideoFrame.from_image(Image.fromarray(frame).resize(video_size))
                    frame.pts = index
                    for packet in stream.encode(frame): container.mux(packet)
                for packet in stream.encode(): container.mux(packet)
            report['files'].append(str(path))
            report['video_dimensions'] = list(video_size)
        (folder / 'manifest.json').write_text(json.dumps(report, indent=2))
        report['manifest'] = str(folder / 'manifest.json')
    samples = torch.from_numpy(np.stack(normalized).astype(np.float32) / 255)
    latest = torch.from_numpy(latest.astype(np.float32) / 255).unsqueeze(0)
    return samples, latest, json.dumps(report)


class StartSessionRecorder:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'session': ('REMOTE_SESSION',),
            'capture': (['all', 'every_nth', 'latest'],),
            'every_n': ('INT', {'default':8, 'min':1, 'max':1000}),
            'max_frames': ('INT', {'default':128, 'min':1, 'max':512}),
            'live_preview': ('BOOLEAN', {'default':True})},
            'hidden': {'unique_id':'UNIQUE_ID'}}
    RETURN_TYPES = ('REMOTE_SESSION', 'SESSION_RECORDING')
    RETURN_NAMES = ('session', 'recording')
    FUNCTION = 'start'
    CATEGORY = 'Livepeer/Remote Session'
    IS_CHANGED = classmethod(lambda cls, **kwargs: float('nan'))

    async def start(self, session, capture, every_n, max_frames, live_preview=True, unique_id=''):
        from .remote_sessions import runtime
        from server import PromptServer
        server = PromptServer.instance
        client_id = getattr(server, 'client_id', None)
        def preview(frame, status):
            image = Image.fromarray(frame)
            image.thumbnail((512,512))
            buffer = io.BytesIO(); image.save(buffer, format='JPEG', quality=75)
            server.send_sync('livepeer-recording-preview', dict(node_id=str(unique_id),
                image=base64.b64encode(buffer.getvalue()).decode(), **status), client_id)
        rt = runtime()
        async def attach():
            s = rt.get(session)
            active = [r for r in s.recorders if not r.stopped]
            s.recorders = active
            previous = getattr(self, 'recording', None)
            config = (session['id'], capture, every_n, max_frames, live_preview)
            if previous in active:
                if config != self.config:
                    raise RuntimeError('Stop the current recording before changing recorder settings')
                return previous
            if len(active) >= 4:
                raise RuntimeError('At most four recorders may capture one session')
            self.recording = Recording(capture, every_n, max_frames, s.config['fps'], preview if live_preview else None)
            self.config = config
            s.recorders.append(self.recording)
            return self.recording
        recording = await rt.call(attach())
        return session, recording


class ReadSessionRecording:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'session': ('REMOTE_SESSION',), 'recording': ('SESSION_RECORDING',),
            'stop': ('BOOLEAN', {'default':True}),
            'save': (['none','images','video','images_and_video'],),
            'filename_prefix': ('STRING', {'default':'livepeer'})}}
    RETURN_TYPES = ('REMOTE_SESSION','IMAGE','IMAGE','STRING')
    RETURN_NAMES = ('session','samples','latest_frame','recording_report')
    FUNCTION = 'read'
    OUTPUT_NODE = True
    CATEGORY = 'Livepeer/Remote Session'
    IS_CHANGED = classmethod(lambda cls, **kwargs: float('nan'))

    async def read(self, session, recording, stop=True, save='none', filename_prefix='livepeer'):
        import folder_paths
        from .remote_sessions import runtime
        rt = runtime()
        async def snapshot():
            s = rt.sessions.get(session['id'])
            if s is None or recording not in s.recorders:
                raise RuntimeError('Recording belongs to a different or removed session')
            if stop: recording.stopped = True
            return recording.snapshot()
        frames, latest, report = await rt.call(snapshot())
        samples, latest, report = await asyncio.to_thread(export, frames, latest, report,
            save, filename_prefix, folder_paths.get_output_directory())
        return {'ui': {'text': [report]}, 'result': (session, samples, latest, report)}


NODE_CLASS_MAPPINGS = {cls.__name__:cls for cls in (StartSessionRecorder, ReadSessionRecording)}
NODE_DISPLAY_NAME_MAPPINGS = {'StartSessionRecorder':'Livepeer · Start Session Recorder',
                            'ReadSessionRecording':'Livepeer · Read / Save Recording'}
