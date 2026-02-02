import { app } from "../../../scripts/app.js";

// Helper to show toast notifications
function showToast(severity, summary, detail, life = 3000) {
    if (app.extensionManager?.toast?.add) {
        app.extensionManager.toast.add({ severity, summary, detail, life });
    }
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
        
        // StartTrickleStream - toast on stream start/stop
        if (nodeData.name === "StartTrickleStream") {
            const onExecuted = nodeType.prototype.onExecuted;
            nodeType.prototype.onExecuted = function (message) {
                onExecuted?.apply(this, arguments);
                if (message.text) {
                    const text = Array.isArray(message.text) ? message.text[0] : message.text;
                    if (text?.includes("Stream connected:")) {
                        showToast("success", "Connected", text, 4000);
                    } else if (text?.includes("Stream stopped")) {
                        showToast("info", "Stopped", text, 3000);
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
                    if (text?.includes("Stream started:")) {
                        showToast("success", "Streaming", text, 4000);
                    } else if (text?.includes("Stream stopped")) {
                        showToast("info", "Stopped", text, 3000);
                    }
                }
            };
        }
    },
});
