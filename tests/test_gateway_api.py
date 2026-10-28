"""The installed gateway package is livepeer/livepeer-python-gateway main."""

from __future__ import annotations

import importlib

import pytest
from livepeer_gateway import MediaOutput, start_lv2v


def test_streaming_api_matches_current_main():
    assert callable(start_lv2v)
    assert callable(MediaOutput.frames)
    assert not hasattr(MediaOutput, "latest_video_frames")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("livepeer_gateway.orchestrator_session")
