# ComfyUI Trickle Streaming Architecture

## Overview

This document describes the architecture of the ComfyUI Trickle Streaming custom node package, which enables real-time video streaming using the Livepeer gateway trickle protocol.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      ComfyUI Workflow                       │
│                                                             │
│  ┌──────────────┐     ┌──────────────┐    ┌─────────────┐│
│  │ TrickleConfig│────▶│StartTrickle  │───▶│TrickleFrame ││
│  │              │     │Stream        │    │Input        ││
│  └──────────────┘     └──────────────┘    └─────────────┘│
│                              │                    │        │
└──────────────────────────────┼────────────────────┼────────┘
                               │                    │
                               ▼                    ▼
                    ┌─────────────────────────────────────┐
                    │    NetworkController (asyncio)      │
                    │    ┌─────────────────────────┐      │
                    │    │    FrameBridge Queue    │      │
                    │    └─────────────────────────┘      │
                    └─────────────────────────────────────┘
                                    │
                                    ▼
                    ┌─────────────────────────────────────┐
                    │   Livepeer Gateway SDK              │
                    │   - MediaPublish (publish frames)   │
                    │   - MediaOutput (subscribe frames)  │
                    └─────────────────────────────────────┘
                                    │
                                    ▼
                    ┌─────────────────────────────────────┐
                    │   Livepeer Orchestrator             │
                    │   (AI inference via trickle)        │
                    └─────────────────────────────────────┘
```

## Core Components

### 1. ComfyUI Nodes (`nodes/frame_nodes.py`)

#### TrickleConfig
Configuration node that outputs a config dict with:
- `orchestrator_url`: Livepeer orchestrator endpoint (https required)
- `signer_url`: Optional signer for authentication
- `model_id`: AI model identifier
- `fps`: Target frame rate
- `keyframe_interval`: Keyframe interval in seconds

#### StartTrickleStream
Starts a trickle stream session and outputs:
- `manifest_id`: Unique stream identifier
- `publish_url`: URL for publishing frames
- `subscribe_url`: URL for subscribing to output
- `error`: Error message if startup failed

**Key features:**
- Proactive health checks every 4 seconds
- Automatic stream restart on failure
- Stream generation tracking for cache invalidation
- Subscriber management for bidirectional streaming

#### TrickleFrameInput
Enqueues frames into the publisher queue.

**Key features:**
- Connects to `publish_url` for execution ordering
- Proactive health checks every 4 seconds
- Raises errors if stream dies (stops workflow)
- Queue depth monitoring

#### TrickleFrameOutput
Retrieves the latest frame from the trickle subscriber.

**Key features:**
- Thread-safe frame access
- Returns blank frame if no subscriber
- Preview display in ComfyUI
- Always re-executes (no caching)

### 2. Network Controller (`nodes/stream/network_controller.py`)

The `NetworkController` manages the trickle publisher lifecycle:

**Stream States:**
- `IDLE`: No stream active
- `STARTING`: Connection in progress (grace period)
- `RUNNING`: Publishing frames normally
- `DEGRADED`: Repeating last frame (no new frames from ComfyUI)
- `ERROR`: Fatal error occurred
- `CLOSED`: Stream ended gracefully

**Publisher Loop:**
1. Wait for frames from FrameBridge
2. Convert to AV frames (yuv420p)
3. Publish via Livepeer SDK
4. Handle errors and state transitions
5. Repeat at target FPS

**Events Monitor:**
- Monitors orchestrator events stream
- Updates stream state based on inference status
- Detects fatal errors (404, stream not found, etc.)
- Triggers graceful shutdown on stream close

### 3. Network Subscriber (`nodes/stream/network_subscriber.py`)

The `NetworkSubscriber` pulls output frames from the orchestrator:

**Key features:**
- Uses `MediaOutput` from Livepeer SDK
- Calls `latest_video_frames()` to skip buffered frames
- Stores latest frame in `TrickleOutputBridge`
- Runs in asyncio loop (background thread)
- Automatic reconnection on errors

### 4. Frame Bridge (`nodes/stream/frame_bridge.py`)

The `FrameBridge` provides a thread-safe queue for sync→async frame passing:

**Architecture:**
```
ComfyUI Node (main thread)
  │
  │ enqueue_tensor_frame(tensor)
  │
  ▼
FrameBridge
  │
  │ (sync) put_nowait → asyncio.Queue
  │
  ▼
NetworkController._publisher_loop() (asyncio thread)
```

**Key features:**
- Pre-loop buffering (stores frames before loop attaches)
- Thread-safe operations
- Queue depth tracking
- Loop rebinding support (for stream restarts)

### 5. Trickle Output Bridge (`nodes/stream/trickle_output_bridge.py`)

The `TrickleOutputBridge` provides thread-safe storage for output frames:

**Architecture:**
```
NetworkSubscriber (asyncio thread)
  │
  │ put_frame_sync(frame)
  │
  ▼
TrickleOutputBridge
  │
  │ thread lock + latest frame storage
  │
  ▼
TrickleFrameOutput (main thread)
  │
  │ get_frame_or_blank_sync()
```

**Key features:**
- Thread-safe read/write
- Latest frame only (no buffering)
- Blank frame fallback
- Timestamp tracking

## Data Flow

### Publishing Flow

```
1. ComfyUI executes nodes
   ↓
2. TrickleFrameInput.push_frame(tensor)
   ↓
3. tensor → uint8 numpy array
   ↓
4. FrameBridge.enqueue(array)
   ↓
5. NetworkController._publisher_loop()
   ↓
6. numpy → av.VideoFrame (yuv420p)
   ↓
7. MediaPublish.write_frame()
   ↓
8. Livepeer orchestrator receives frame
```

### Subscribing Flow

```
1. NetworkSubscriber._consume()
   ↓
2. MediaOutput.latest_video_frames()
   ↓
3. Decode av.VideoFrame → numpy
   ↓
4. TrickleOutputBridge.put_frame_sync()
   ↓
5. TrickleFrameOutput.pull_frame()
   ↓
6. Display in ComfyUI preview
```

## Configuration

### Network Configuration Resolution

The `nodes/stream/credentials.py` module provides simple URL resolution:

```python
def resolve_network_config(
    orchestrator_url: str = "",
    signer_url: str = "",
) -> Tuple[str, str]:
    """
    Preference order:
    1. Explicit parameters (from TrickleConfig node)
    2. Environment variables
    3. Hardcoded defaults
    """
```

### Configuration Resolution

Orchestrator/signer URLs are resolved from:
1. **TrickleConfig node inputs** (primary method)
2. **Environment variables**: `ORCHESTRATOR_URL`, `SIGNER_URL`
3. **Hardcoded defaults** (for development)

## Thread Safety

### Synchronization Points

1. **FrameBridge**:
   - ComfyUI nodes (main thread) → `enqueue()` → `asyncio.run_coroutine_threadsafe()`
   - NetworkController (asyncio thread) → `queue.get()`

2. **TrickleOutputBridge**:
   - NetworkSubscriber (asyncio thread) → `put_frame_sync()` → `threading.Lock()`
   - TrickleFrameOutput (main thread) → `get_frame_sync()` → `threading.Lock()`

3. **Settings/Credentials**:
   - All credential operations use `threading.Lock()` in `credentials_store.py`

## Error Handling

### Stream Lifecycle Errors

**Fatal errors (trigger ERROR state):**
- 404 (stream not found)
- POST failed
- Encoder failed
- MediaPublish errors

**Recoverable errors:**
- Temporary network issues
- Frame encoding glitches
- Events stream transient failures

### Node Error Handling

**TrickleFrameInput:**
- Raises `RuntimeError` if stream died → stops workflow
- Logs warnings if stream not started yet
- Proactive health checks prevent silent failures

**StartTrickleStream:**
- Returns error in `error` output
- Clears controller on fatal errors
- Tracks `last_startup_error` for downstream nodes

## Performance Characteristics

### Frame Queue

- **Max queue size**: 90 frames (~3 seconds at 30fps)
- **Buffering**: Frames buffered before loop attach
- **Drop policy**: Drop oldest when queue full

### Publisher Timing

- **Frame interval**: `1.0 / fps` seconds
- **Frame repeat**: Last frame repeated if queue empty
- **Drift prevention**: Next frame time adjusted on large delays

### Subscriber Timing

- **Latest only**: Skips buffered frames, always shows newest
- **No blocking**: Non-blocking reads from output bridge

## Testing

### Unit Tests

- Located in `tests/` directory
- Run with `pytest tests/`
- Requires `pytest` and `pytest-mock`

### Integration Testing

Manual workflow testing:
1. Create workflow with Trickle nodes
2. Connect to test orchestrator
3. Verify frames publish/subscribe
4. Monitor logs for errors

## Migration Notes

### Removed Components

The following components have been removed:
- Legacy WebRTC/WHIP/WHEP nodes
- DayDream API integration
- Pipeline configuration nodes (StreamDiffusion params now passed via orchestrator)
- ControlNet configuration nodes
- JavaScript UI extensions (sidebar, toolbar)

### Folder Restructure

- `rtc_stream/` → `nodes/stream/`
- All imports updated to `nodes.stream.*`
- Logger names changed from `rtc_stream.*` to `comfyui_trickle.*`
- Removed `nodes/pipeline_config.py` and `nodes/controlnet.py`
- Removed `nodes/settings_storage.py`
- Removed `server/` directory

## Future Enhancements

Potential improvements:
- [ ] Control message support via `UpdateTrickleStream`
- [ ] Multiple concurrent streams
- [ ] Stream quality metrics/monitoring
- [ ] Automatic reconnection on network failures
- [ ] Pipeline parameter hot-reload

## References

- [Livepeer Gateway Python SDK Documentation](https://github.com/livepeer/livepeer-python-gateway)
- [ComfyUI Custom Node Development](https://docs.comfy.org/)
- [StreamDiffusion Pipeline](https://github.com/cumulo-autumn/StreamDiffusion)
