import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

// Helper to show toast notifications
function showToast(severity, summary, detail, life = 3000) {
    if (app.extensionManager?.toast?.add) {
        app.extensionManager.toast.add({ severity, summary, detail, life });
    }
}

// Registry of live viewers so we can reset them when streams stop.
function registerLivepeerViewer(node, iframe) {
    if (!window.__livepeerViewers) {
        window.__livepeerViewers = [];
    }
    window.__livepeerViewers.push({ node, iframe });
}

function resetAllLivepeerViewers() {
    if (!window.__livepeerViewers) return;
    window.__livepeerViewers = window.__livepeerViewers.filter(({ iframe, node }) => {
        if (!iframe) return false;
        try {
            iframe.src = "about:blank";
            iframe.srcdoc = `<html><body style="margin:0;background:#000;"></body></html>`;
        } catch (_err) {
            // ignore
        }
        if (node) {
            node._viewerUrl = "";
            if (typeof node.setDirtyCanvas === "function") {
                node.setDirtyCanvas(true, true);
            }
        }
        return true;
    });
}

// Reset scheduling is disabled to avoid blanking the player during rapid re-runs.
function scheduleViewerReset(_delayMs = 1500) {}
function cancelScheduledViewerReset() {}

function isLivepeerAuthRequiredMessage(message) {
    const text = String(message || "");
    return (
        text.includes("Livepeer login required before starting stream.") ||
        text.includes("Click Livepeer Login in Settings")
    );
}

async function startLivepeerLoginFlow(updateStatus) {
    updateStatus("Preparing browser login...");
    const response = await api.fetchApi("/livepeer/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
        throw new Error(data.error || `HTTP ${response.status}`);
    }

    if (data.authenticated) {
        updateStatus("Already logged in. You can start streaming now.");
        return;
    }

    if (!data.pending || !data.auth_url) {
        throw new Error("Login could not be started.");
    }

    window.open(data.auth_url, "_blank", "noopener,noreferrer");
    const codeText = data.user_code ? ` Code: ${data.user_code}.` : "";
    updateStatus(`Complete login in browser tab.${codeText} Waiting for confirmation...`);

    for (let attempt = 0; attempt < 180; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        const statusResponse = await api.fetchApi("/livepeer/auth/status", { method: "GET" });
        const statusData = await statusResponse.json().catch(() => ({}));
        if (!statusResponse.ok || !statusData.ok) {
            throw new Error(statusData.error || `HTTP ${statusResponse.status}`);
        }
        if (statusData.authenticated) {
            updateStatus("Login successful. Re-run StartTrickleStream.", true);
            return;
        }
        if (statusData.login_error) {
            throw new Error(statusData.login_error);
        }
        if (!statusData.login_in_progress) {
            throw new Error("Login did not complete. Please try again.");
        }
        if (statusData.auth_url) {
            const liveCodeText = statusData.user_code ? ` Code: ${statusData.user_code}.` : "";
            updateStatus(`Complete login in browser.${liveCodeText} Waiting for confirmation...`);
        }
    }
    throw new Error("Login timed out. Please try again.");
}

function showLivepeerLoginModal(errorMessage = "") {
    if (window.__livepeerLoginModalOpen) {
        return;
    }
    window.__livepeerLoginModalOpen = true;

    const overlay = document.createElement("div");
    overlay.style.position = "fixed";
    overlay.style.inset = "0";
    overlay.style.background = "rgba(0,0,0,0.55)";
    overlay.style.display = "flex";
    overlay.style.alignItems = "center";
    overlay.style.justifyContent = "center";
    overlay.style.zIndex = "100001";

    const modal = document.createElement("div");
    modal.style.width = "min(520px, calc(100vw - 32px))";
    modal.style.background = "#121826";
    modal.style.border = "1px solid #2a3550";
    modal.style.borderRadius = "10px";
    modal.style.padding = "16px";
    modal.style.boxShadow = "0 20px 60px rgba(0,0,0,0.45)";
    modal.style.color = "#eef3ff";

    const title = document.createElement("div");
    title.textContent = "Login to Livepeer";
    title.style.fontSize = "18px";
    title.style.fontWeight = "600";
    title.style.marginBottom = "8px";

    const subtitle = document.createElement("div");
    subtitle.textContent = "Your Livepeer session is expired. Sign in to continue streaming.";
    subtitle.style.opacity = "0.9";
    subtitle.style.marginBottom = "10px";

    const details = document.createElement("div");
    details.style.fontSize = "12px";
    details.style.opacity = "0.82";
    details.style.marginBottom = "12px";
    if (errorMessage) {
        details.textContent = errorMessage;
    }

    const status = document.createElement("div");
    status.style.fontSize = "13px";
    status.style.minHeight = "18px";
    status.style.marginBottom = "14px";
    status.style.color = "#b8ffe0";

    const actions = document.createElement("div");
    actions.style.display = "flex";
    actions.style.justifyContent = "flex-end";
    actions.style.gap = "8px";

    const cancelBtn = document.createElement("button");
    cancelBtn.className = "p-button p-component p-button-sm";
    cancelBtn.textContent = "Close";
    cancelBtn.style.background = "#24314d";
    cancelBtn.style.border = "1px solid #3a4a72";
    cancelBtn.style.color = "#d7e2ff";

    const loginBtn = document.createElement("button");
    loginBtn.className = "p-button p-component p-button-sm";
    loginBtn.textContent = "Login in Browser";

    const closeModal = () => {
        overlay.remove();
        window.__livepeerLoginModalOpen = false;
    };

    cancelBtn.onclick = closeModal;
    overlay.onclick = (event) => {
        if (event.target === overlay) {
            closeModal();
        }
    };

    loginBtn.onclick = async () => {
        loginBtn.disabled = true;
        loginBtn.textContent = "Starting...";
        const setStatus = (text, success = false) => {
            status.textContent = text;
            status.style.color = success ? "#9cffb0" : "#b8ffe0";
        };
        try {
            await startLivepeerLoginFlow(setStatus);
            showToast("success", "Livepeer Login", "Login successful. Re-run StartTrickleStream.", 5000);
            loginBtn.textContent = "Logged In";
        } catch (error) {
            const msg = error instanceof Error ? error.message : String(error);
            setStatus(`Login failed: ${msg}`);
            loginBtn.textContent = "Retry Login";
            loginBtn.disabled = false;
            showToast("error", "Livepeer Login Failed", msg, 10000);
        }
    };

    actions.appendChild(cancelBtn);
    actions.appendChild(loginBtn);
    modal.appendChild(title);
    modal.appendChild(subtitle);
    modal.appendChild(details);
    modal.appendChild(status);
    modal.appendChild(actions);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);
}

function toAbsoluteUrl(url) {
    try {
        return new URL(url, window.location.origin).toString();
    } catch (_error) {
        return url;
    }
}

function ensureTrickleIframeWidget(node) {
    if (!node || node.__livepeerIframeReady) {
        return;
    }
    node.__livepeerIframeReady = true;
    if (typeof node.addDOMWidget !== "function") {
        return;
    }

    const container = document.createElement("div");
    container.style.display = "flex";
    container.style.flexDirection = "column";
    container.style.gap = "6px";
    container.style.width = "100%";
    container.style.height = "100%";
    container.style.padding = "0";

    const label = document.createElement("div");
    label.textContent = "Livepeer Output Preview";
    label.style.fontSize = "11px";
    label.style.color = "#8ff6bb";
    label.style.fontFamily = "monospace";

    const iframe = document.createElement("iframe");
    iframe.style.width = "100%";
    iframe.style.height = "100%";
    iframe.style.flex = "1 1 auto";
    iframe.style.minHeight = "0";
    iframe.style.border = "1px solid #2f3f36";
    iframe.style.borderRadius = "6px";
    iframe.style.background = "#000";
    iframe.setAttribute("allow", "fullscreen");
    iframe.setAttribute("loading", "lazy");

    container.appendChild(label);
    container.appendChild(iframe);

    const computeWidgetSize = () => {
        const nodeHeight = Array.isArray(node.size) ? Number(node.size[1] || 0) : 0;
        // Width is fluid (100% of widget area). Height tracks node height with tiny offset.
        const width = 0;
        // Reserve room for title + native widgets; fallback when node size isn't ready yet.
        const height = nodeHeight > 0
            ? Math.max(200, Math.floor(nodeHeight - 150))
            : 220;
        return {
            width,
            height,
        };
    };

    const applyIframeSize = () => {
        const nextSize = computeWidgetSize();
        if (node.__livepeerIframeWidget?.options) {
            node.__livepeerIframeWidget.options.width = nextSize.width;
            node.__livepeerIframeWidget.options.height = nextSize.height;
        }
        try {
            node.__livepeerIframeWidget.width = nextSize.width;
        } catch (_error) {
            // Some ComfyUI builds expose readonly width/height.
        }
        try {
            node.__livepeerIframeWidget.height = nextSize.height;
        } catch (_error) {
            // Some ComfyUI builds expose readonly width/height.
        }
        iframe.style.width = "100%";
        iframe.style.height = "100%";
        container.style.width = "100%";
        container.style.height = "100%";
    };

    const initialSize = computeWidgetSize();
    const widget = node.addDOMWidget(
        "livepeer_iframe_player",
        "livepeer_iframe_player",
        container,
        {
            hideOnZoom: false,
            serialize: false,
            width: 0,
            height: initialSize.height,
            getValue: () => node._viewerUrl || "",
            setValue: (value) => {
                const nextUrl = String(value || "");
                node._viewerUrl = nextUrl;
                iframe.src = nextUrl ? toAbsoluteUrl(nextUrl) : "about:blank";
            },
        },
    );
    widget.computeSize = () => {
        const nextSize = computeWidgetSize();
        return [Math.max(320, (node.size?.[0] || 340) - 24), nextSize.height];
    };
    applyIframeSize();

    node.__livepeerIframe = iframe;
    node.__livepeerIframeWidget = widget;
    node.__livepeerApplyIframeSize = applyIframeSize;
    node.__livepeerLastSizeKey = "";

    // Re-apply after initial layout settles (node.size can be stale at create time).
    requestAnimationFrame(() => applyIframeSize());
    setTimeout(() => applyIframeSize(), 50);

    registerLivepeerViewer(node, iframe);
}

function showDeviceAuthPrompt(payload) {
    const authUrl = String(payload?.auth_url || "").trim();
    const userCode = String(payload?.user_code || "").trim();
    const expiresIn = Number(payload?.expires_in || 0);
    const interrupted = Boolean(payload?.workflow_interrupted);
    const expiresMinutes = expiresIn > 0 ? Math.max(1, Math.round(expiresIn / 60)) : null;
    const dedupeKey = `${authUrl}|${userCode}`;

    if (window.__livepeerLastDeviceAuthPrompt === dedupeKey) {
        return;
    }
    window.__livepeerLastDeviceAuthPrompt = dedupeKey;

    const details = [
        "Livepeer login required to continue this workflow.",
        interrupted ? "Workflow execution was interrupted until login is complete." : "Workflow is waiting for authorization to complete.",
        authUrl ? `Open: ${authUrl}` : "",
        userCode ? `Code: ${userCode}` : "",
        expiresMinutes ? `Expires in ~${expiresMinutes} minute(s).` : "",
    ].filter(Boolean);

    showToast("warn", "Livepeer Login Required", details.join(" "), 20000);

    // Auto-open browser tab once per device code so user immediately sees login page.
    if (authUrl && window.__livepeerLastDeviceAuthAutoOpened !== dedupeKey) {
        window.__livepeerLastDeviceAuthAutoOpened = dedupeKey;
        window.open(authUrl, "_blank", "noopener,noreferrer");
    }
}

if (!window.__livepeerDeviceAuthListenerRegistered) {
    window.__livepeerDeviceAuthListenerRegistered = true;
    api.addCustomEventListener("livepeer_device_auth_required", (event) => {
        showDeviceAuthPrompt(event?.detail || {});
    });
}

function showLivepeerExecutionError(event) {
    const detail = event?.detail || {};
    const message = String(detail?.exception_message || "");
    if (!message) {
        return;
    }

    if (!isLivepeerAuthRequiredMessage(message)) {
        return;
    }

    const dedupeKey = message;
    if (window.__livepeerLastExecutionErrorToast === dedupeKey) {
        return;
    }
    window.__livepeerLastExecutionErrorToast = dedupeKey;

    showToast(
        "error",
        "Livepeer Login Required",
        message,
        12000,
    );
    showLivepeerLoginModal(message);
}

if (!window.__livepeerExecutionErrorListenerRegistered) {
    window.__livepeerExecutionErrorListenerRegistered = true;
    api.addEventListener("execution_error", (event) => {
        showLivepeerExecutionError(event);
    });
}

// Livepeer node UI extensions
app.registerExtension({
    name: "Livepeer.NodeExtensions",
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        
        // TrickleStreamURL - display URL in node widget
        if (nodeData.name === "TrickleStreamURL") {
            const onExecuted = nodeType.prototype.onExecuted;
            nodeType.prototype.onExecuted = function (message) {
                onExecuted?.apply(this, arguments);
                
                if (message.text) {
                    const urlText = Array.isArray(message.text) ? message.text[0] : message.text;
                    if (!urlText) return;
                    
                    // Store the URL for display
                    this._streamUrl = urlText;
                    
                    // Find existing display widget or create one
                    let displayWidget = this.widgets?.find(w => w.name === "stream_url_display");
                    
                    if (!displayWidget) {
                        displayWidget = {
                            name: "stream_url_display",
                            type: "customtext",
                            value: urlText,
                            options: { serialize: false },
                            draw: function(ctx, node, width, y, height) {
                                const url = node._streamUrl || "";
                                if (!url) return;
                                
                                ctx.fillStyle = "#1a1a2e";
                                ctx.fillRect(10, y, width - 20, height);
                                ctx.strokeStyle = "#4a9eff";
                                ctx.lineWidth = 1;
                                ctx.strokeRect(10, y, width - 20, height);
                                ctx.fillStyle = "#4a9eff";
                                ctx.font = "12px monospace";
                                ctx.textAlign = "left";
                                ctx.textBaseline = "middle";
                                
                                const maxWidth = width - 30;
                                let displayUrl = url;
                                if (ctx.measureText(url).width > maxWidth) {
                                    displayUrl = url.substring(0, 35) + "...";
                                }
                                ctx.fillText(displayUrl, 15, y + height / 2);
                            },
                            computeSize: function() {
                                return [200, 26];
                            },
                            mouse: function(event, pos, node) {
                                if (event.type === "pointerdown" && node._streamUrl) {
                                    navigator.clipboard.writeText(node._streamUrl).then(() => {
                                        showToast("success", "Copied", "Stream URL copied to clipboard", 2000);
                                    }).catch(err => {
                                        console.error("Failed to copy URL:", err);
                                    });
                                    return true;
                                }
                                return false;
                            }
                        };
                        
                        if (!this.widgets) this.widgets = [];
                        this.widgets.push(displayWidget);
                    } else {
                        displayWidget.value = urlText;
                    }
                    
                    requestAnimationFrame(() => {
                        this.setSize(this.computeSize());
                        app.graph.setDirtyCanvas(true, false);
                    });
                }
            };
        }

        if (nodeData.name === "TrickleBrowserPlayer") {
            nodeType.size = [560, 520];
            nodeType.resizable = true;

            const onNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                this.resizable = true;
                if (this.flags) {
                    this.flags.resizable = true;
                }
                if (!Array.isArray(this.size) || this.size[0] < 420 || this.size[1] < 360) {
                    this.size = [560, 520];
                }
                const result = onNodeCreated?.apply(this, arguments);
                ensureTrickleIframeWidget(this);
                if (typeof this.__livepeerApplyIframeSize === "function") {
                    this.__livepeerApplyIframeSize();
                }
                return result;
            };

            const onResize = nodeType.prototype.onResize;
            nodeType.prototype.onResize = function (size) {
                const result = onResize?.apply(this, arguments);
                if (typeof this.__livepeerApplyIframeSize === "function") {
                    this.__livepeerApplyIframeSize();
                }
                if (typeof this.setDirtyCanvas === "function") {
                    this.setDirtyCanvas(true, true);
                }
                return result;
            };

            const onDrawForeground = nodeType.prototype.onDrawForeground;
            nodeType.prototype.onDrawForeground = function (ctx) {
                const result = onDrawForeground?.apply(this, arguments);
                if (typeof this.__livepeerApplyIframeSize === "function" && Array.isArray(this.size)) {
                    const sizeKey = `${this.size[0]}x${this.size[1]}`;
                    if (sizeKey !== this.__livepeerLastSizeKey) {
                        this.__livepeerLastSizeKey = sizeKey;
                        this.__livepeerApplyIframeSize();
                    }
                }
                return result;
            };

            const onExecuted = nodeType.prototype.onExecuted;
            nodeType.prototype.onExecuted = function (message) {
                onExecuted?.apply(this, arguments);
                ensureTrickleIframeWidget(this);
                if (!message?.text) {
                    return;
                }
                const textEntries = Array.isArray(message.text) ? message.text : [String(message.text)];
                const text = textEntries.join(" ");
                if (text.startsWith("ERROR:")) {
                    showToast("error", "Trickle Browser Player Error", text, 12000);
                    return;
                }

                const viewerLine = textEntries.find((entry) => String(entry).startsWith("Iframe URL:"));
                const viewerUrl = viewerLine ? String(viewerLine).replace("Iframe URL:", "").trim() : "";
                if (!viewerUrl) {
                    return;
                }

                this._viewerUrl = viewerUrl;
                if (this.__livepeerIframe) {
                    const absoluteViewerUrl = toAbsoluteUrl(viewerUrl);
                    if (this.__livepeerIframe.src !== absoluteViewerUrl) {
                        this.__livepeerIframe.src = absoluteViewerUrl;
                    }
                }
                if (typeof this.__livepeerApplyIframeSize === "function") {
                    this.__livepeerApplyIframeSize();
                }
                requestAnimationFrame(() => app.graph.setDirtyCanvas(true, false));
            };
        }

        if (nodeData.name === "TrickleStartAndPreview") {
            nodeType.size = [560, 520];
            nodeType.resizable = true;

            const onNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                this.resizable = true;
                if (this.flags) {
                    this.flags.resizable = true;
                }
                if (!Array.isArray(this.size) || this.size[0] < 420 || this.size[1] < 360) {
                    this.size = [560, 520];
                }
                const result = onNodeCreated?.apply(this, arguments);
                ensureTrickleIframeWidget(this);
                if (typeof this.__livepeerApplyIframeSize === "function") {
                    this.__livepeerApplyIframeSize();
                }
                return result;
            };

            const onResize = nodeType.prototype.onResize;
            nodeType.prototype.onResize = function (size) {
                const result = onResize?.apply(this, arguments);
                if (typeof this.__livepeerApplyIframeSize === "function") {
                    this.__livepeerApplyIframeSize();
                }
                if (typeof this.setDirtyCanvas === "function") {
                    this.setDirtyCanvas(true, true);
                }
                return result;
            };

            const onDrawForeground = nodeType.prototype.onDrawForeground;
            nodeType.prototype.onDrawForeground = function (ctx) {
                const result = onDrawForeground?.apply(this, arguments);
                if (typeof this.__livepeerApplyIframeSize === "function" && Array.isArray(this.size)) {
                    const sizeKey = `${this.size[0]}x${this.size[1]}`;
                    if (sizeKey !== this.__livepeerLastSizeKey) {
                        this.__livepeerLastSizeKey = sizeKey;
                        this.__livepeerApplyIframeSize();
                    }
                }
                return result;
            };

            const onExecuted = nodeType.prototype.onExecuted;
            nodeType.prototype.onExecuted = function (message) {
                onExecuted?.apply(this, arguments);
                ensureTrickleIframeWidget(this);
                if (!message?.text) {
                    return;
                }
                const textEntries = Array.isArray(message.text) ? message.text : [String(message.text)];
                const text = textEntries.join(" ");
                if (text.startsWith("ERROR:")) {
                    showToast("error", "Trickle Start + Preview Error", text, 12000);
                    return;
                }

                const viewerLine = textEntries.find((entry) => String(entry).startsWith("Iframe URL:"));
                const viewerUrl = viewerLine ? String(viewerLine).replace("Iframe URL:", "").trim() : "";
                if (!viewerUrl) {
                    return;
                }

                this._viewerUrl = viewerUrl;
                if (this.__livepeerIframe) {
                    const absoluteViewerUrl = toAbsoluteUrl(viewerUrl);
                    if (this.__livepeerIframe.src !== absoluteViewerUrl) {
                        this.__livepeerIframe.src = absoluteViewerUrl;
                    }
                }
                if (typeof this.__livepeerApplyIframeSize === "function") {
                    this.__livepeerApplyIframeSize();
                }
                requestAnimationFrame(() => app.graph.setDirtyCanvas(true, false));
            };
        }
        
        // StartTrickleStream - toast on stream start/stop
        if (nodeData.name === "StartTrickleStream") {
            const onExecuted = nodeType.prototype.onExecuted;
            nodeType.prototype.onExecuted = function (message) {
                onExecuted?.apply(this, arguments);
                if (message.text) {
                    const text = Array.isArray(message.text) ? message.text[0] : message.text;
                    const dedupeKey = `start:${text}`;
                    if (text?.includes("Stream connected:")) {
                        if (window.__livepeerLastStartToast === dedupeKey) return;
                        window.__livepeerLastStartToast = dedupeKey;
                        cancelScheduledViewerReset();
                        showToast("success", "Connected", text, 4000);
                    } else if (text?.startsWith("ERROR:")) {
                        showToast("error", "Start Trickle Stream Error", text, 12000);
                        if (isLivepeerAuthRequiredMessage(text)) {
                            showLivepeerLoginModal(text);
                        }
                    } else if (text?.includes("Stream stopped")) {
                        showToast("info", "Stopped", text, 3000);
                        scheduleViewerReset();
                    }
                }
            };
        }
        
        // LoadVideoStream - toast on video stream start/stop
        if (nodeData.name === "LoadVideoStream") {
            const onExecuted = nodeType.prototype.onExecuted;
            nodeType.prototype.onExecuted = function (message) {
                onExecuted?.apply(this, arguments);
                if (message.text) {
                    const text = Array.isArray(message.text) ? message.text[0] : message.text;
                    const dedupeKey = `load:${text}`;
                    if (text?.includes("Stream started:")) {
                        if (window.__livepeerLastLoadToast === dedupeKey) return;
                        window.__livepeerLastLoadToast = dedupeKey;
                        cancelScheduledViewerReset();
                        showToast("success", "Streaming", text, 4000);
                    } else if (text?.startsWith("ERROR:")) {
                        showToast("error", "Load Video Stream Error", text, 12000);
                    } else if (text?.includes("Stream stopped")) {
                        showToast("info", "Stopped", text, 3000);
                        scheduleViewerReset();
                    }
                }
            };
        }
    },
});
