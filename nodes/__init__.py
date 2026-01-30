import logging
import sys

from . import frame_nodes, stream_url_node, js


def _configure_logging():
    base_logger = logging.getLogger("comfyui_trickle")
    has_handler = any(getattr(handler, "_comfyui_trickle_handler", False) for handler in base_logger.handlers)
    if not has_handler:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        handler._comfyui_trickle_handler = True
        base_logger.addHandler(handler)
    base_logger.setLevel(logging.INFO)
    base_logger.propagate = True


_configure_logging()


NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

NODE_CLASS_MAPPINGS.update(frame_nodes.NODE_CLASS_MAPPINGS)
NODE_DISPLAY_NAME_MAPPINGS.update(frame_nodes.NODE_DISPLAY_NAME_MAPPINGS)
NODE_CLASS_MAPPINGS.update(stream_url_node.NODE_CLASS_MAPPINGS)
NODE_DISPLAY_NAME_MAPPINGS.update(stream_url_node.NODE_DISPLAY_NAME_MAPPINGS)
NODE_CLASS_MAPPINGS.update(js.NODE_CLASS_MAPPINGS)
NODE_DISPLAY_NAME_MAPPINGS.update(js.NODE_DISPLAY_NAME_MAPPINGS)


__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]

