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

No environment variables are required. The node ships the production PymtHouse issuer and public client id, and a signed-in session chooses an orchestrator from discovery.

### 1. Sign in from ComfyUI

Open **ComfyPeer account** in the lower-right of the ComfyUI canvas and select **Sign in**. The extension starts [RFC 8628](https://www.rfc-editor.org/rfc/rfc8628) device authorization, opens the ComfyPeer approval page, and shows the approval code plus your current usage/balance. It is an account control, not a workflow node.

Sign-in tokens live only in this process. **Sign out** clears them. Restarting ComfyUI also signs you out, because nothing is written to disk or to the workflow. Sign in again after a restart.

The PymtHouse app behind this client is configured as follows. Keep third-party initiate login **on**.

| PymtHouse app setting | Value |
|---|---|
| Client type | Public (no client secret) |
| Client id | `app_444013b9d984f62f5572a282` |
| Grant types | `urn:ietf:params:oauth:grant-type:device_code` ([RFC 8628](https://www.rfc-editor.org/rfc/rfc8628)), `refresh_token`, and token exchange for the signer session ([RFC 8693](https://www.rfc-editor.org/rfc/rfc8693)) |
| Scopes | `openid`, `sign:job` |
| Third-party initiate login | On |
| Initiate login URI | `https://www.comfypeer.com/login` |
| Redirect handling | Device approval resumes at `/login?iss=…&target_link_uri=…`. The page validates both before showing the code. |

`PYMTHOUSE_PUBLIC_CLIENT_ID` and the legacy `PYMTHOUSE_CLIENT_ID` remain optional aliases. `COMFYPEER_PUBLIC_CLIENT_ID` wins when it is set.

`TrickleConfig` uses the signed-in ComfyPeer account by default. Each job obtains a fresh signer session from the user's device-flow JWT. The fallback signer is used only if **Use ComfyPeer account** is disabled.

### 2. Download a starter workflow

Published graphs are hosted by ComfyPeer. This package links there. After you install the nodes, drag a downloaded graph onto the canvas.

| Graph | Download |
|---|---|
| Stream a video file (passthrough) | https://www.comfypeer.com/go/workflow?id=stream-video-file-noop |
| Stream a video file (SDXL) | https://www.comfypeer.com/go/workflow?id=stream-video-file-sdxl |
| Stream webcam frames (passthrough) | https://www.comfypeer.com/go/workflow?id=stream-video-frames-noop |
| Stream webcam frames (SDXL) | https://www.comfypeer.com/go/workflow?id=stream-video-frames-sdxl |
| Image I/O | https://www.comfypeer.com/go/workflow?id=image-io |
| Text to speech | https://www.comfypeer.com/go/workflow?id=livepeer-text-to-speech |

The same files are also in `workflows/` so ComfyUI can list them from the installed node. ComfyPeer is the copy those links serve. Leave the orchestrator field blank.

### 3. Build a Streaming Workflow

**Basic webcam→orchestrator workflow:**

1. Add `WebcamCapture` node (from ComfyUI core)
2. Add `TrickleConfig` node:
   - Leave **Orchestrator URL** blank. Discovery picks an orchestrator that has capacity, otherwise the first HTTPS orchestrator in the signed-in inventory
   - Set a URL only when you want to override discovery
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

The ComfyPeer account control is a global ComfyUI extension button, separate from the graph. It uses the built-in ComfyPeer public client for device authorization and never requires a machine-client secret on the desktop.

1. Open **ComfyPeer account** and click **Sign in**. The issuer `https://pymthouse.com/api/v1/oidc` and client id `app_444013b9d984f62f5572a282` are built in.
2. Approve the device code in the browser. This process polls the token endpoint, exchanges the user access token for a signer session ([RFC 8693](https://www.rfc-editor.org/rfc/rfc8693)), and reads `GET /api/v1/user/usage` plus `GET /api/v1/user/usage/balance` with that user's JWT.
3. Build the graph from modular I/O: a frame source feeds `TrickleFrameInput`, `StartTrickleStream` creates the remote job, and `TrickleFrameOutput` supplies returned frames to any ComfyUI image operation. `StreamDiffusion SDXL Config` feeds parameters and its `model_id_out` selects `streamdiffusion-sdxl` automatically.
4. Start the workflow. With the orchestrator field blank, the signed-in session's discovery inventory chooses the orchestrator. Payments use the short-lived signer URL and bearer derived from the signed-in account. `comfystream` and other enabled remote model IDs can be selected directly on `TrickleConfig`.

The signer JWT lasts about five minutes. This process refreshes the user access token with the refresh-token grant, exchanges it for a new signer JWT, and updates the bearer dict the gateway payment session already holds. Tokens stay only in memory. They are never returned to the browser UI or workflow JSON, and **Sign out** clears them. Restarting ComfyUI drops that memory, so the account panel is signed out until you sign in again.

When the usage or balance payload includes an `explain` string for `at_risk` or `blocked`, the account panel shows that text. Other billing states are left unchanged, and the panel does not invent its own billing copy.

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
- **nodes/stream/credentials.py**: Orchestrator override and discovery selection; signer URL resolution
- **nodes/stream/trickle_output_bridge.py**: Thread-safe output frame storage

## Configuration

Nothing in this section is required for a Desktop install. These variables override the built-in defaults.

### Overrides

| Variable | When it applies |
|---|---|
| `PYMTHOUSE_ISSUER_URL` | Replaces `https://pymthouse.com/api/v1/oidc`. A present but blank value is rejected. |
| `COMFYPEER_PUBLIC_CLIENT_ID` | Replaces `app_444013b9d984f62f5572a282`. A present but blank value is rejected. |
| `PYMTHOUSE_PUBLIC_CLIENT_ID` | Alias used only when `COMFYPEER_PUBLIC_CLIENT_ID` is absent. |
| `PYMTHOUSE_CLIENT_ID` | Older alias used only when both names above are absent. |
| `ORCHESTRATOR_URL` | Explicit orchestrator. Used only when the TrickleConfig field is blank (or still the old `https://localhost:8935` placeholder). Discovery is not consulted. |
| `SIGNER_URL` | Optional fallback signer when **Use ComfyPeer account** is off. |
| `COMFYPEER_DISCOVERY_URL` | Staging discovery URL. Production uses the URL from the signer session. |
| `COMFYPEER_OFFERINGS_URL` | Staging offerings catalog. When unset, the node describes the discovery inventory alone. |

`TrickleConfig.orchestrator_url` is the same kind of override as `ORCHESTRATOR_URL`. Leave it blank to use discovery. An explicit URL wins over both the environment variable and discovery. The old default `https://localhost:8935` is treated as blank so existing graphs do not pin localhost.

Discovery prefers the first HTTPS orchestrator that has a runner with `capacity_available > 0`. If none are free, it uses the first HTTPS address. If the inventory has no HTTPS orchestrator, the stream fails with:

```text
No orchestrator available right now, try again
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
│   └── js/                     # Canvas extensions (account, discovery, stream URL)
├── workflows/                  # Installed examples; downloads are on ComfyPeer
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

1. Leave the orchestrator blank and sign in, or set an explicit HTTPS orchestrator URL
2. `No orchestrator available right now, try again` means discovery returned no HTTPS orchestrator. Retry in a minute
3. Check the fallback signer URL only when **Use ComfyPeer account** is disabled
4. Look for errors in the ComfyUI console
5. Check orchestrator logs

### Frame not publishing

1. Ensure `publish_url` is connected from `StartTrickleStream` to `TrickleFrameInput`
2. Check if stream is healthy in logs
3. Verify frames are reaching the FrameBridge (check queue depth logs)

## License

This repository does not include a license file.

## Design notes

- The public issuer and client id are built in so ComfyUI Desktop can sign in without a shell environment. Environment variables remain overrides for staging and self-hosting.
- A key that is present but blank is an explicit override and is rejected. A missing key uses the built-in value. That keeps a cleared variable from silently looking like "use the default" when the operator meant to disable it.
- Orchestrator choice is discovery from the signed-in session. An explicit URL is an override, not a requirement. There is no built-in orchestrator host.
- Preview catalog hosts are not defaults. Production reads the session discovery URL. `COMFYPEER_DISCOVERY_URL` and `COMFYPEER_OFFERINGS_URL` exist for staging.
- Account tokens are memory-only. A process restart is a sign-out.
- Billing copy for `at_risk` and `blocked` is shown only when the usage or balance payload already includes `explain`. The panel does not add billing states of its own.

## Credits

Built with:
- [Livepeer Gateway Python SDK](https://github.com/livepeer/livepeer-python-gateway)
- [ComfyUI](https://github.com/Comfy-Org/ComfyUI)
