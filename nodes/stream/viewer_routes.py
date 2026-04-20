from __future__ import annotations

import json
import logging
from urllib.parse import unquote

from aiohttp import web
from server import PromptServer

LOGGER = logging.getLogger("comfyui_trickle.viewer_routes")


@PromptServer.instance.routes.get("/livepeer/viewer")
async def livepeer_stream_viewer(request: web.Request) -> web.Response:
    stream_param = str(request.query.get("stream", "")).strip()
    stream_url = unquote(stream_param) if stream_param else "http://127.0.0.1:8765/stream"
    stream_url_json = json.dumps(stream_url)

    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Livepeer Trickle Viewer</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #080b10;
      --fg: #d7deea;
      --muted: #9aa8bd;
      --accent: #5cb6ff;
      --err: #ff6b6b;
      --ok: #5de17d;
      --panel: #101622;
    }
    body {
      margin: 0;
      background: radial-gradient(circle at 20% 0%, #101a2a 0%, var(--bg) 45%);
      color: var(--fg);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      height: 100vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    header {
      padding: 6px 10px;
      border-bottom: 1px solid #1e2a3f;
      background: rgba(12, 18, 31, 0.8);
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: nowrap;
      min-height: 28px;
    }
    .status-dot {
      width: 10px;
      height: 10px;
      border-radius: 999px;
      background: var(--muted);
    }
    .status-dot.ok {
      background: var(--ok);
      box-shadow: 0 0 8px rgba(93, 225, 125, 0.6);
    }
    .status-dot.err {
      background: var(--err);
      box-shadow: 0 0 8px rgba(255, 107, 107, 0.6);
    }
    #status {
      color: var(--muted);
      font-size: 12px;
    }
    #stream {
      color: var(--accent);
      font-size: 12px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      opacity: 0.9;
      flex: 1;
    }
    main {
      flex: 1;
      min-height: 0;
      padding: 0;
      display: flex;
    }
    .video-wrap {
      width: 100%;
      height: 100%;
      background: #000;
      border-radius: 0;
      border: 1px solid #2a4a7a; /* subtle blue frame */
      overflow: hidden;
    }
    video {
      width: 100%;
      height: 100%;
      object-fit: cover;
      background: #000;
    }
  </style>
</head>
<body>
  <header>
    <div id="dot" class="status-dot"></div>
    <div id="status">Initializing player...</div>
    <div id="stream"></div>
  </header>
  <main>
    <div class="video-wrap">
      <video
        id="video"
        autoplay
        muted
        playsinline
        controlslist="nofullscreen noremoteplayback nodownload noplaybackrate"
      ></video>
    </div>
  </main>

  <script src="https://cdn.jsdelivr.net/npm/mpegts.js@1.8.0/dist/mpegts.min.js"></script>
  <script>
    const streamUrl = __STREAM_URL_JSON__;
    const video = document.getElementById("video");
    const dot = document.getElementById("dot");
    const status = document.getElementById("status");
    const stream = document.getElementById("stream");
    stream.textContent = streamUrl;

    let player = null;
    let retries = 0;
    let allowReconnect = true;

    function setStatus(text, state = "idle") {
      status.textContent = text;
      dot.classList.remove("ok", "err");
      if (state === "ok") dot.classList.add("ok");
      if (state === "err") dot.classList.add("err");
    }

    function cleanupPlayer() {
      if (!player) return;
      try { player.destroy(); } catch (_e) {}
      player = null;
    }

    function scheduleReconnect(delayMs = 1200) {
      if (!allowReconnect) {
        setStatus("Stream ended.", "err");
        return;
      }
      retries += 1;
      const wait = Math.min(delayMs * retries, 5000);
      setStatus(`Reconnecting... attempt ${retries}`, "err");
      setTimeout(startPlayer, wait);
    }

    function startPlayer() {
      cleanupPlayer();
      if (!window.mpegts || !mpegts.getFeatureList().mseLivePlayback) {
        setStatus("This browser cannot play live MPEG-TS via MSE.", "err");
        return;
      }
      setStatus("Connecting to stream...");
      try {
        player = mpegts.createPlayer(
          {
            type: "mpegts",
            url: streamUrl,
            isLive: true,
            hasAudio: true,
            hasVideo: true,
          },
          {
            enableStashBuffer: false,
            autoCleanupSourceBuffer: true,
            liveBufferLatencyChasing: true,
          }
        );
        player.attachMediaElement(video);
        player.load();
        player.play().catch(() => {});
        video.controls = false;
        const preventPause = () => {
          if (player) {
            player.play().catch(() => {});
          } else {
            video.play().catch(() => {});
          }
        };
        video.addEventListener("pause", preventPause);
        video.addEventListener("click", (event) => {
          event.preventDefault();
          preventPause();
        });
        player.on(mpegts.Events.MEDIA_INFO, () => {
          retries = 0;
          setStatus("Live stream active", "ok");
        });
        player.on(mpegts.Events.ERROR, (_type, _detail, info) => {
          console.warn("Player error", info);
          scheduleReconnect();
        });
        video.addEventListener("ended", () => {
          allowReconnect = false;
          setStatus("Stream ended.", "err");
          cleanupPlayer();
        });
      } catch (error) {
        console.error("Failed to start player", error);
        scheduleReconnect();
      }
    }

    window.addEventListener("beforeunload", cleanupPlayer);
    startPlayer();
  </script>
</body>
</html>
"""
    html = html.replace("__STREAM_URL_JSON__", stream_url_json)

    return web.Response(
        text=html,
        content_type="text/html",
    )
