# Trickle Streaming Tests

## Overview

This test suite validates the trickle streaming functionality for ComfyUI custom nodes.

## Test Structure

```
tests/
├── __init__.py
├── requirements.txt
└── README.md (this file)
```

## Running Tests

```bash
# Install test dependencies
pip install -r tests/requirements.txt

# Run all tests
pytest tests/

# Run with verbose output
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=nodes --cov=nodes.stream
```

## Test Dependencies

Core dependencies (see `requirements.txt`):
- `pytest` - Test framework
- `pytest-mock` - Mocking utilities
- All dependencies from main `requirements.txt`

## Manual Testing

### Workflow Testing

Test with example workflows:

1. **Start ComfyUI:**
   ```bash
   cd /path/to/ComfyUI
   python main.py
   ```

2. **Load workflow:**
   Open `custom_nodes/livepeer-ai-nodes/workflows/Trickle streaming workflow.json`

3. **Configure TrickleConfig node:**
   - `orchestrator_url`: `https://your-orchestrator:8936`
   - `signer_url`: `https://signer.eliteencoder.net` (optional)
   - `model_id`: `noop` (or `comfystream`)

4. **Queue the workflow**

5. **Monitor console for:**
   - `[SUBSCRIBER]` messages
   - `publish_url` and `subscribe_url`
   - Frame queue depth
   - Stream state transitions

### Testing Components Individually

**Test FrameBridge:**
```python
from nodes.stream.frame_bridge import FRAME_BRIDGE, enqueue_array_frame
import numpy as np

# Create test frame
frame = np.zeros((512, 512, 3), dtype=np.uint8)
enqueue_array_frame(frame)
print(f"Queue depth: {FRAME_BRIDGE.depth()}")
```

**Test NetworkController:**
```python
from nodes.stream.network_controller import NetworkController, NetworkControllerConfig

config = NetworkControllerConfig(
    orchestrator_url="https://test-orch:8936",
    model_id="noop",
    fps=30.0,
)

controller = NetworkController(config)
try:
    status = controller.start(model_id="noop", params={})
    print(f"Stream started: {status['publish_url']}")
finally:
    controller.stop()
```

## Testing Checklist

When testing changes:

- [ ] All nodes appear in ComfyUI node menu
- [ ] TrickleConfig accepts orchestrator/signer URLs
- [ ] StartTrickleStream creates stream and returns URLs
- [ ] TrickleFrameInput enqueues frames without errors
- [ ] TrickleFrameOutput displays received frames
- [ ] Stream health checks work correctly
- [ ] Stream stops cleanly when disabled
- [ ] Error messages are clear and actionable
- [ ] No import errors on ComfyUI startup
- [ ] Logs show expected state transitions

## Common Test Scenarios

### 1. Basic Publish Flow

Test that frames flow from nodes to orchestrator:

1. Create workflow with WebcamCapture → TrickleFrameInput
2. Start stream with StartTrickleStream
3. Verify frames enqueued (check logs)
4. Verify frames published (check orchestrator)

### 2. Subscribe Flow

Test that output frames are received:

1. Start stream with subscriber enabled (subscribe_url present)
2. Add TrickleFrameOutput node
3. Verify frames displayed in output preview
4. Check subscriber logs for frame receipt

### 3. Error Handling

Test graceful error handling:

1. Start stream with invalid orchestrator URL
2. Verify error appears in StartTrickleStream error output
3. Verify TrickleFrameInput raises clear error
4. Test recovery by fixing URL and re-running

### 4. Stream Lifecycle

Test start/stop/restart:

1. Start stream (enabled=True)
2. Verify stream running
3. Stop stream (enabled=False)
4. Verify clean shutdown
5. Restart stream
6. Verify new URLs generated

## Debugging Tips

### Node Import Issues

If nodes don't load:
```bash
# Check Python syntax
python -m py_compile nodes/frame_nodes.py

# Clear cache
find . -name "__pycache__" -type d -exec rm -rf {} +
find . -name "*.pyc" -delete
```

### Stream Connection Issues

Check logs for:
- `NetworkController event loop started`
- `Fetching orchestrator info`
- `Starting job model_id=...`
- `NetworkController started publish_url=...`

### Frame Flow Issues

Check logs for:
- `FrameBridge queued frame`
- `Publisher loop exit`
- `Stream state transitions`

## CI/CD Integration

Future: Add GitHub Actions workflow for automated testing.

```yaml
# .github/workflows/test.yml (example)
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      - run: pip install -r requirements.txt -r tests/requirements.txt
      - run: pytest tests/ --cov
```

## Documentation Testing

Verify documentation stays in sync:
- [ ] All node names match code
- [ ] All file paths are correct
- [ ] Example code snippets work
- [ ] Architecture diagrams reflect current state
