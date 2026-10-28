# ComfyUI Trickle Streaming

Real-time streaming for ComfyUI using the Livepeer gateway trickle protocol. Frames are published directly to orchestrators and outputs are pulled via trickle subscribe.

## Features

- 🎥 **Trickle publish/subscribe**: send/receive frames via Livepeer network
- 🔄 **Bidirectional streaming nodes**: start, input, and output nodes for ComfyUI
- ⚙️ **Configurable orchestrator/signer**: point at any Livepeer orchestrator, optional remote signer
- 🔐 **PymtHouse sign-in**: public device login, signer-session refresh, and usage on one node
- 🧱 **FrameBridge**: sync→async bridge for safe node enqueue
- 💡 **Pure Livepeer**: direct connection to orchestrators, no middleware required

## Installation

### ComfyUI Desktop (Recommended)

1. **Install via ComfyUI Manager** (easiest method):
   - Open ComfyUI Desktop
   - Go to **Manager** → **Install Custom Nodes**
   - Search for "Livepeer AI Streaming"
   - Click **Install**
   - Restart ComfyUI

2. **Install via comfy-cli** (ComfyUI Registry):
   ```bash
   comfy node registry-install livepeer-ai-nodes
   ```
   Then restart ComfyUI.

3. **Manual Installation**:
   ```bash
   cd %USERPROFILE%\Documents\ComfyUI\custom_nodes
   git clone https://github.com/eliteprox/livepeer-ai-nodes.git livepeer-ai-nodes
   cd livepeer-ai-nodes
   pip install -r requirements.txt
   ```
   Then restart ComfyUI Desktop.

### Standalone ComfyUI Installation

For standard ComfyUI installations:

```bash
cd /path/to/ComfyUI/custom_nodes
git clone https://github.com/eliteprox/livepeer-ai-nodes.git livepeer-ai-nodes
cd livepeer-ai-nodes
pip install -r requirements.txt
```

Restart your ComfyUI server.

### Verify Installation

After installation, you should see new nodes in the **"Trickle"** category.

## Quick Start

### 1. Configure Orchestrator and PymtHouse

Set environment variables:
```bash
# Required: orchestrator URL
export ORCHESTRATOR_URL=https://your-orchestrator.example.com:8936

# Optional: remote signer used only when nobody is signed in
export SIGNER_URL=https://signer.example

# PymtHouse public OIDC client. PYMTHOUSE_CLIENT_ID is the public app_ id.
export PYMTHOUSE_ISSUER_URL=https://your-pymthouse.example/api/v1/oidc
export PYMTHOUSE_CLIENT_ID=app_your_public_client_id
```

The PymtHouse app must allow the device-code grant (`urn:ietf:params:oauth:grant-type:device_code`), the refresh-token grant, and the scopes `openid` and `sign:job`. Leave third-party initiate login off so the Sign in button opens PymtHouse's own device page.

Orchestrator and fallback signer URLs can also be set on the `TrickleConfig` node. When a PymtHouse session is active, the stream uses that session's signer URL and bearer instead of the pasted signer URL.

### 2. Build a Streaming Workflow

**Basic webcam→orchestrator workflow:**

1. Add `WebcamCapture` node (from ComfyUI core)
2. Add `TrickleConfig` node:
   - Set orchestrator URL
   - Set signer URL (optional)
   - Set model ID (e.g., "noop" or "comfystream")
   - Set FPS and keyframe interval
3. Add `StartTrickleStream` node:
   - Connect config from `TrickleConfig`
   - Set width/height for the stream
4. Add `TrickleFrameInput` node:
   - Connect IMAGE from webcam
   - Connect publish_url from `StartTrickleStream`
5. (Optional) Add `TrickleFrameOutput` node to view output frames

### 3. Run Your Workflow

- Queue the workflow in ComfyUI
- Frames will be published to the orchestrator via trickle
- Check the console logs for `publish_url` and `subscribe_url`
- Use the `subscribe_url` to view the output stream

## Available Nodes

### Trickle Category

- **TrickleConfig**: Configuration for orchestrator connection
- **StartTrickleStream**: Start a trickle stream session
- **TrickleFrameInput**: Enqueue frames to publish
- **TrickleFrameOutput**: Receive frames from subscribe URL
- **UpdateTrickleStream**: Send control messages to running stream
- **PymtHouse Login**: Sign in with a public device flow and show usage

## PymtHouse sign-in

`PymtHouse Login` is a ComfyUI node. Its **Sign in** button is a frontend control, not a workflow execution.

1. Set `PYMTHOUSE_ISSUER_URL` and `PYMTHOUSE_CLIENT_ID` in the environment that launches ComfyUI.
2. Add the **PymtHouse Login** node.
3. Click **Sign in**. The node asks this process to start [RFC 8628](https://www.rfc-editor.org/rfc/rfc8628) device authorization against the public client, then opens a local page that redirects to the device verification URL on the configured issuer.
4. Approve the code in the browser. The node polls the token endpoint, exchanges the user access token for a signer session ([RFC 8693](https://www.rfc-editor.org/rfc/rfc8693)), and reads `GET /api/v1/user/usage` plus `GET /api/v1/user/usage/balance`.
5. Start a trickle stream. Payments use the signer URL from the session and `Authorization: Bearer` set to the signer JWT.

The signer JWT lasts about five minutes. This process refreshes the user access token with the refresh-token grant, exchanges it for a new signer JWT, and updates the bearer dict the gateway payment session already holds. Tokens stay in memory. Restarting ComfyUI requires Sign in again.

No machine-client secret is stored here. Signer-session exchange authenticates with the user access token alone.

## Architecture

```
ComfyUI Node (sync)
  └─> FrameBridge (thread-safe queue)
       └─> NetworkController (asyncio loop)
            └─> Livepeer Gateway SDK
                 └─> Orchestrator (trickle publish/subscribe)
```

### Key Components

- **nodes/frame_nodes.py**: All 5 trickle streaming nodes
- **nodes/stream/network_controller.py**: Trickle publisher (asyncio)
- **nodes/stream/network_subscriber.py**: Trickle subscriber (asyncio)
- **nodes/stream/frame_bridge.py**: Thread-safe sync→async frame queue
- **nodes/stream/credentials.py**: Simple orchestrator/signer URL resolution
- **nodes/stream/trickle_output_bridge.py**: Thread-safe output frame storage

## Configuration

### Environment Variables

```bash
# Livepeer network
ORCHESTRATOR_URL=https://orchestrator.example.com:8936
SIGNER_URL=https://signer.example  # optional fallback when not signed in

# PymtHouse public client
PYMTHOUSE_ISSUER_URL=https://your-pymthouse.example/api/v1/oidc
PYMTHOUSE_CLIENT_ID=app_your_public_client_id
```


## Development

### Running Tests

```bash
pip install -r tests/requirements.txt
pytest tests/
```

### Project Structure

```
livepeer-ai-nodes/
├── nodes/
│   ├── frame_nodes.py          # All 5 trickle streaming nodes
│   ├── stream/                 # Streaming internals (5 modules)
│   │   ├── network_controller.py
│   │   ├── network_subscriber.py
│   │   ├── frame_bridge.py
│   │   ├── credentials.py
│   │   └── trickle_output_bridge.py
│   └── js/                     # Empty (UI extensions removed)
├── workflows/                  # Example workflow
├── tests/                      # Test framework
└── docs/                       # Documentation
```

## Troubleshooting

### Nodes not loading

1. Check ComfyUI console for import errors
2. Verify Python dependencies: `pip install -r requirements.txt`
3. Clear Python cache: delete all `__pycache__` folders
4. Restart ComfyUI

### Stream connection issues

1. Verify orchestrator URL is accessible (https required)
2. Check signer URL if authentication required
3. Look for errors in ComfyUI console
4. Check orchestrator logs

### Frame not publishing

1. Ensure `publish_url` is connected from `StartTrickleStream` to `TrickleFrameInput`
2. Check if stream is healthy in logs
3. Verify frames are reaching the FrameBridge (check queue depth logs)

## License

[Your License Here]

## Credits

Built with:
- [Livepeer Gateway Python SDK](https://github.com/livepeer/livepeer-python-gateway)
- [ComfyUI](https://github.com/Comfy-Org/ComfyUI)
