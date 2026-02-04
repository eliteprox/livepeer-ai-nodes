"""
ComfyUI Trickle Streaming custom nodes package initialization.

Ensures ComfyUI discovers custom nodes under `nodes`.
"""

import sys
import logging
from pathlib import Path

LOGGER = logging.getLogger("livepeer-ai-nodes")

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

WEB_DIRECTORY = "./nodes/js"

# Import nodes - let errors propagate so they're visible in ComfyUI logs
from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

LOGGER.info("livepeer-ai-nodes loaded %d nodes: %s", len(NODE_CLASS_MAPPINGS), list(NODE_CLASS_MAPPINGS.keys()))

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
]
