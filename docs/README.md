# Documentation

## Overview

This directory contains detailed documentation for the ComfyUI Trickle Streaming custom node package.

## Documents

### [ARCHITECTURE.md](./ARCHITECTURE.md)

Technical architecture documentation covering:
- System architecture and data flow
- Core components (nodes, controllers, bridges)
- Thread safety and synchronization
- Error handling strategies
- Performance characteristics
- Testing guidelines

## Quick Links

- [Main README](../README.md) - Installation and quick start guide
- [Workflows](../workflows/) - Example workflow files
- [Tests](../tests/) - Unit tests and test documentation

## Key Concepts

### Trickle Protocol

The trickle protocol is a lightweight streaming protocol used by Livepeer for real-time media publishing and subscribing:
- **Publish**: Upload frames to orchestrator via HTTP chunks
- **Subscribe**: Download processed frames via HTTP chunks
- **Stateless**: No WebRTC signaling required

### Frame Bridge

Thread-safe queue that bridges ComfyUI's synchronous execution with the asyncio-based streaming controller:
- Accepts frames from ComfyUI nodes (main thread)
- Provides frames to publisher loop (asyncio thread)
- Buffers frames when loop not ready
- Resets cleanly between stream sessions

### Network Controller

Manages the streaming lifecycle:
- Creates asyncio loop in background thread
- Publishes frames at target FPS
- Monitors orchestrator events
- Handles errors and state transitions
- Provides health checks for nodes

### Pipeline Configuration

Validates and formats StreamDiffusion pipeline parameters:
- Model selection
- Prompts and guidance
- ControlNet attachments
- Inference parameters
- Schema validation

## Development

### Adding New Nodes

1. Define node class in `nodes/frame_nodes.py` or new module
2. Implement `INPUT_TYPES`, `RETURN_TYPES`, `FUNCTION`
3. Add to `NODE_CLASS_MAPPINGS` and `NODE_DISPLAY_NAME_MAPPINGS`
4. Set appropriate `CATEGORY` (use "Trickle" prefix)
5. Add tests if applicable

### Debugging

**Enable debug logging:**
```python
import logging
logging.getLogger("comfyui_trickle").setLevel(logging.DEBUG)
```

**Check logs:**
- Frame bridge queue depth
- Publisher/subscriber state changes
- Network errors
- Frame timing

## API Reference

### Frame Bridge API

```python
from nodes.stream.frame_bridge import (
    enqueue_tensor_frame,  # Enqueue ComfyUI tensor
    enqueue_array_frame,   # Enqueue numpy array
    queue_depth,           # Get current queue size
    has_loop,              # Check if asyncio loop attached
    FRAME_BRIDGE,          # Global bridge instance
)
```

### Network Controller API

```python
from nodes.stream.network_controller import (
    NetworkController,
    NetworkControllerConfig,
)

config = NetworkControllerConfig(
    orchestrator_url="https://orch.example.com:8936",
    signer_url="http://signer.example.com:8081",
    model_id="noop",
    fps=30.0,
    frame_width=512,
    frame_height=512,
    keyframe_interval_s=2.0,
)

controller = NetworkController(config)
status = controller.start(model_id="noop", params={})
print(status["publish_url"])
```

### Network Configuration API

```python
from nodes.stream.credentials import resolve_network_config

orch_url, signer_url = resolve_network_config(
    orchestrator_url="https://custom-orch:8936",
    signer_url="http://custom-signer:8081",
)
```

Resolves orchestrator and signer URLs from:
1. Explicit parameters (from TrickleConfig node)
2. Environment variables (`ORCHESTRATOR_URL`, `SIGNER_URL`)
3. Hardcoded defaults

## Contributing

When contributing to this project:

1. Follow existing code style
2. Add tests for new features
3. Update documentation
4. Keep imports at the top of files
5. Use type hints where applicable
6. Add docstrings to public APIs

## Support

For issues and feature requests:
- Check [Issues](../../issues) on GitHub
- Review [Discussions](../../discussions)
- See [Troubleshooting](../README.md#troubleshooting) in main README
