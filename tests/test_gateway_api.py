"""The node targets the current livepeer-python-gateway live-runner API."""

from __future__ import annotations

import importlib

import pytest
from livepeer_gateway import MediaOutput, reserve_session, stop_runner_session


def test_streaming_api_matches_current_main():
    assert callable(reserve_session)
    assert callable(stop_runner_session)
    assert callable(MediaOutput.frames)
    assert not hasattr(MediaOutput, "latest_video_frames")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("livepeer_gateway.lv2v")
