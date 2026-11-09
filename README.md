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

# Optional: self-managed signer used only when a workflow disables the account
export SIGNER_URL=https://signer.example

# ComfyPeer's PymtHouse public OIDC client. This app_ ID is not a secret.
export PYMTHOUSE_ISSUER_URL=https://your-pymthouse.example/api/v1/oidc
export COMFYPEER_PUBLIC_CLIENT_ID=app_your_comfypeer_public_client_id
```

The PymtHouse app must allow the device-code grant (`urn:ietf:params:oauth:grant-type:device_code`), the refresh-token grant, and the scopes `openid` and `sign:job`. Leave third-party initiate login off so the ComfyPeer account button opens PymtHouse's own device page. `PYMTHOUSE_PUBLIC_CLIENT_ID` and the legacy `PYMTHOUSE_CLIENT_ID` remain supported as environment-variable aliases.

`TrickleConfig` uses the signed-in ComfyPeer account by default. Each job obtains a fresh signer session from the user's device-flow JWT; the fallback signer is used only if **Use ComfyPeer account** is disabled.

### 2. Sign in from ComfyUI

Open **ComfyPeer account** in the lower-right of the ComfyUI canvas and select **Sign in**. The extension starts [RFC 8628](https://www.rfc-editor.org/rfc/rfc8628) device authorization, opens the issuer's verified device URL, and shows the approval code plus your current usage/balance. It is an account control, not a workflow node.

### 3. Build a Streaming Workflow

**Basic webcam→orchestrator workflow:**

1. Add `WebcamCapture` node (from ComfyUI core)
2. Add `TrickleConfig` node:
   - Set orchestrator URL
   - Leave **Use ComfyPeer account** enabled for the signed-in user's billing
   - Set model ID (for example, `streamdiffusion-sdxl` or `comfystream`)
   - Set FPS and keyframe interval
   - Connect `model_id_out` from a pipeline configuration to `pipeline_model_id` to select the pipeline automatically
3. Add `StartTrickleStream` node:
   - Connect config from `TrickleConfig`
   - Set width/height for the stream
4. Add `TrickleFrameInput` node:
   - Connect IMAGE from webcam
   - Connect publish_url from `StartTrickleStream`
5. (Optional) Add `TrickleFrameOutput` node to view output frames

### 4. Run Your Workflow

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
- **ComfyPeer account control**: Global Sign in, usage refresh, and Sign out; it does not alter or need a graph

## ComfyPeer account and user-paid streams

The ComfyPeer account control is a global ComfyUI extension button, separate from the graph. It uses the ComfyPeer public client for device authorization and never requires a machine-client secret on the desktop.

1. Set `PYMTHOUSE_ISSUER_URL` and `COMFYPEER_PUBLIC_CLIENT_ID` in the environment that launches ComfyUI.
2. Open **ComfyPeer account** and click **Sign in**.
3. Approve the device code in the browser. This process polls the token endpoint, exchanges the user access token for a signer session ([RFC 8693](https://www.rfc-editor.org/rfc/rfc8693)), and reads `GET /api/v1/user/usage` plus `GET /api/v1/user/usage/balance` with that user's JWT.
4. Build the graph from modular I/O: a frame source feeds `TrickleFrameInput`, `StartTrickleStream` creates the remote job, and `TrickleFrameOutput` supplies returned frames to any ComfyUI image operation. `StreamDiffusion SDXL Config` feeds parameters and its `model_id_out` selects `streamdiffusion-sdxl` automatically.
5. Start the workflow. Payments use the short-lived signer URL and bearer derived from the signed-in account. `comfystream` and other enabled remote model IDs can be selected directly on `TrickleConfig`.

The signer JWT lasts about five minutes. This process refreshes the user access token with the refresh-token grant, exchanges it for a new signer JWT, and updates the bearer dict the gateway payment session already holds. Tokens stay only in memory, are never returned to the browser UI or workflow JSON, and are cleared by **Sign out**. Restarting ComfyUI requires Sign in again.

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
SIGNER_URL=https://signer.example  # optional fallback when account use is disabled

# ComfyPeer public client (app_ ID, not a secret)
PYMTHOUSE_ISSUER_URL=https://your-pymthouse.example/api/v1/oidc
COMFYPEER_PUBLIC_CLIENT_ID=app_your_comfypeer_public_client_id
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
