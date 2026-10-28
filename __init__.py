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

# ComfyUI loads this directory as a package. Pytest also imports this file while
# collecting tests, but the checkout directory name is not a Python package name,
# so that import has no parent package. Keep the ComfyUI import strict.
if __package__:
    from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

    LOGGER.info(
        "livepeer-ai-nodes loaded %d nodes: %s",
        len(NODE_CLASS_MAPPINGS),
        list(NODE_CLASS_MAPPINGS.keys()),
    )
else:
    NODE_CLASS_MAPPINGS = {}
    NODE_DISPLAY_NAME_MAPPINGS = {}

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
]
