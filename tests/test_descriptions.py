"""Descriptions built from discovery envelopes and the pinned registry."""
import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "livepeer_descriptions",
    Path(__file__).resolve().parents[1] / "nodes" / "descriptions.py",
)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
build_request = _module.build_request
describe_discovery = _module.describe_discovery
describe_offerings = _module.describe_offerings
find_choice = _module.find_choice

MUSIC = {
    "runner_contract": 1,
    "deployment_revision": "sha256:" + "a" * 64,
    "model": {"name": "ACE-Step/Ace-Step1.5", "source": "hf:ACE-Step/Ace-Step1.5", "revision": "rev"},
    "endpoints": {"music_generations": {"profile": "comfystream.music.v1"}},
    "runtime": {"image": "sha256:" + "b" * 64},
}
IMAGE = {
    "runner_contract": 1,
    "deployment_revision": "sha256:" + "c" * 64,
    "model": {"name": "black-forest-labs/FLUX.2-klein-4B", "source": "hf:black-forest-labs/FLUX.2-klein-4B"},
    "endpoints": {"images": {"profile": "openai.images.v1"}},
    "runtime": {"image": "sha256:" + "d" * 64},
}
LIVE = {
    "runner_contract": 1,
    "deployment_revision": "sha256:" + "e" * 64,
    "model": {"name": "stabilityai/sd-turbo", "source": "hf:stabilityai/sd-turbo"},
    "endpoints": {"stream": {"profile": "comfystream.live.v1", "stop": "/stop",
                             "channels": {"in": ["video"], "out": ["video"]}}},
    "runtime": {"image": "sha256:" + "f" * 64},
}
SCALE = {
    "runner_contract": 1,
    "deployment_revision": "sha256:" + "1" * 64,
    "endpoints": {"scale_video": {"profile": "comfystream.ffmpeg.v1"}},
    "runtime": {"image": "sha256:" + "2" * 64},
}
OLD = {"inference_contract": 2, "model_name": "old", "endpoints": {"images_generations": {"profile": "gateway.music.v1"}}}


def runner(app, metadata, url, unit="fixed"):
    return {
        "url": url, "app": app, "mode": "single-shot", "capacity_available": 1,
        "metadata": json.dumps(metadata, separators=(",", ":")),
        "price_info": {"price": 1, "currency": "wei", "unit": unit},
    }


def catalog():
    return describe_discovery([
        {"address": "https://ai.example:8936", "runners": [
            runner("music", MUSIC, "https://ai.example/apps/music/app"),
            runner("text-to-image", IMAGE, "https://ai.example/apps/image/app"),
            runner("live/sd-turbo", LIVE, "https://ai.example/apps/sd/session", unit="seconds"),
            runner("media/ffmpeg-scale-video", SCALE, "https://ai.example/apps/scale/app"),
            runner("ace-step", OLD, "https://ai.example/apps/old/app"),
        ]},
    ])


def test_choices_come_from_the_registry_not_the_app_string():
    described = catalog()
    labels = described["apps"]
    assert "music · ACE-Step/Ace-Step1.5" in labels
    assert "text-to-image · black-forest-labs/FLUX.2-klein-4B" in labels
    assert "live-video-to-video · stabilityai/sd-turbo" in labels
    assert "media-tools · scale video" in labels
    assert any(item["runner_id"] or item["reason"] for item in described["ignored"])

    music = find_choice(described, "music")
    assert music["call"] == {
        "method": "POST", "path": "/v1/music/generations", "content_type": "application/json",
        "framing": ["json"], "channels": None, "stop": None, "idle_timeout_s": None, "drain_timeout_s": None,
        "inputs": music["call"]["inputs"], "outputs": music["call"]["outputs"],
    }
    assert [field["name"] for field in music["fields"]] == ["prompt", "lyrics", "seconds", "model"]
    assert build_request(music, {"prompt": "a horn", "seconds": "12"})["seconds"] == 12
    assert build_request(music, {"prompt": "a horn"})["model"] == "ACE-Step/Ace-Step1.5"

    live = find_choice(described, "live/sd-turbo")
    assert live["capability"] == "live-video-to-video"
    assert live["call"]["path"] == "/stream"
    assert live["call"]["channels"] == {"in": ["video"], "out": ["video"]}
    assert live["call"]["stop"] == "/stop"
    assert live["fields"] == []
    assert build_request(live, {}, prompt="glow", seconds=8, timeout=30)["prompt"] == "glow"

    scale = find_choice(described, "media-tools · scale video")
    assert scale["call"]["path"] == "/batch/scale-video"
    assert any(field["name"] == "video_url" and field["required"] for field in scale["fields"])


def _rate(unit, price):
    return {
        "amount": price, "currency": "USD", "unit": unit, "price_usd": price,
        "source": "orchestrator", "observed_at": "2026-11-09T00:00:00Z", "observed_by": "orchestrator",
    }


def test_offerings_are_one_choice_per_app():
    offerings = [
        {"app": "music", "modes": ["single-shot"], "rate_low": _rate("fixed", "0.02"),
         "rate_high": _rate("fixed", "0.04"), "rate_status": "available", "stale": False,
         "runner_count": 2, "ready_count": 2},
        {"app": "transcription", "modes": ["single-shot"], "rate_low": _rate("seconds", "0.001"),
         "rate_high": _rate("seconds", "0.002"), "rate_status": "available", "stale": False,
         "runner_count": 1, "ready_count": 1},
        {"app": "transcription", "modes": ["single-shot"], "rate_low": _rate("minute", "0.06"),
         "rate_high": _rate("minute", "0.06"), "rate_status": "available", "stale": False,
         "runner_count": 1, "ready_count": 1},
        {"app": "ace-step", "modes": ["single-shot"], "rate_low": None, "rate_high": None,
         "rate_status": "missing", "stale": True, "runner_count": 1, "ready_count": 0},
        {"app": "live/sd-turbo", "modes": ["persistent"], "rate_low": _rate("seconds", "0.01"),
         "rate_high": _rate("seconds", "0.02"), "rate_status": "available", "stale": False,
         "runner_count": 1, "ready_count": 1},
    ]
    discovery = [
        {"address": "https://ai1.example:8936", "runners": [
            runner("music", MUSIC, "https://ai1.example/apps/music/app"),
            runner("ace-step", OLD, "https://ai1.example/apps/old/app"),
            runner("text-to-image", IMAGE, "https://ai1.example/apps/image/app"),
            runner("live/sd-turbo", LIVE, "https://ai1.example/apps/sd/session", unit="seconds"),
        ]},
        {"address": "https://ai2.example:8936", "runners": [
            runner("music", MUSIC, "https://ai2.example/apps/music/app"),
        ]},
    ]
    described = describe_offerings(offerings, discovery)
    assert described["apps"].count("music") == 1
    assert "text-to-image" not in described["apps"]

    music = find_choice(described, "music")
    assert music["profile"] == "comfystream.music.v1"
    assert [field["name"] for field in music["fields"]] == ["prompt", "lyrics", "seconds", "model"]
    assert music["call"]["path"] == "/v1/music/generations"
    assert sorted(item["url"] for item in music["runners"]) == [
        "https://ai1.example/apps/music/app",
        "https://ai2.example/apps/music/app",
    ]
    assert "0.02 USD/fixed" in music["summary"]

    transcription = find_choice(described, "transcription")
    assert [item["low"]["unit"] for item in transcription["rates"]] == ["seconds", "minute"]
    assert transcription["fields"] == []

    old = find_choice(described, "ace-step")
    assert old["fields"] == []
    assert old["call"] is None
    assert old["runners"] == []
    assert old["message"] == "No runner_contract metadata for this app."
    assert old["stale"] is True
    assert old["rate_status"] == "missing"

    live = find_choice(described, "live/sd-turbo")
    assert live["call"]["path"] == "/stream"
    assert live["endpoint"] == "stream"


TRANSCRIPT = {
    "runner_contract": 1,
    "deployment_revision": "sha256:" + "3" * 64,
    "model": {"name": "whisper-1", "source": "hf:openai/whisper"},
    "endpoints": {"transcriptions": {"profile": "openai.transcriptions.v1"}},
    "runtime": {"image": "sha256:" + "4" * 64},
}
SPEECH = {
    "runner_contract": 1,
    "deployment_revision": "sha256:" + "5" * 64,
    "model": {"name": "tts-1", "source": "hf:openai/tts"},
    "endpoints": {"speech": {"profile": "openai.speech.v1"}},
    "runtime": {"image": "sha256:" + "6" * 64},
}


def test_audio_jobs_upload_a_file_or_return_speech_bytes():
    described = describe_offerings(
        [
            {"app": "transcription", "modes": ["single-shot"], "rate_low": _rate("seconds", "0.001"),
             "rate_high": _rate("seconds", "0.002"), "rate_status": "available", "stale": False,
             "runner_count": 1, "ready_count": 1},
            {"app": "text-to-speech", "modes": ["single-shot"], "rate_low": _rate("seconds", "0.001"),
             "rate_high": _rate("seconds", "0.002"), "rate_status": "available", "stale": False,
             "runner_count": 1, "ready_count": 1},
            {"app": "music", "modes": ["single-shot"], "rate_low": _rate("fixed", "0.02"),
             "rate_high": _rate("fixed", "0.04"), "rate_status": "available", "stale": False,
             "runner_count": 1, "ready_count": 1},
        ],
        [{"address": "https://ai.example:8936", "runners": [
            runner("transcription", TRANSCRIPT, "https://ai.example/apps/transcription/app", unit="seconds"),
            runner("text-to-speech", SPEECH, "https://ai.example/apps/speech/app", unit="seconds"),
            runner("music", MUSIC, "https://ai.example/apps/music/app"),
        ]}],
    )
    transcript = find_choice(described, "transcription")
    speech = find_choice(described, "text-to-speech")
    music = find_choice(described, "music")
    assert _module.is_audio_choice(transcript)
    assert _module.is_audio_choice(speech)
    assert not _module.is_audio_choice(music)
    assert transcript["call"]["content_type"] == "multipart/form-data"
    assert transcript["call"]["path"] == "/v1/audio/transcriptions"
    assert any(field["name"] == "file" and field["required"] for field in transcript["fields"])
    assert speech["call"]["path"] == "/v1/audio/speech"
    assert "binary" in speech["call"]["framing"]

    body, content_type = _module.multipart_body({"model": "whisper-1", "file": "audio.wav"}, b"RIFF")
    assert content_type.startswith("multipart/form-data; boundary=")
    assert b'name="model"' in body and b"whisper-1" in body
    assert b'name="file"; filename="audio.wav"' in body and b"RIFF" in body
    assert b'name="file"\r\n\r\naudio.wav' not in body
