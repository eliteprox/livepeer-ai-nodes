import { app } from "../../../scripts/app.js";

const LOGIN_PATH = "/livepeer-ai-nodes/pymthouse/login";
const STATUS_PATH = "/livepeer-ai-nodes/pymthouse/status";
const VERIFICATION_PATH = "/livepeer-ai-nodes/pymthouse/verification";

function showToast(severity, summary, detail, life = 4000) {
    if (app.extensionManager?.toast?.add) {
        app.extensionManager.toast.add({ severity, summary, detail, life });
    }
}

function statusText(payload) {
    if (!payload) {
        return "Not signed in";
    }
    if (payload.message) {
        return payload.message;
    }
    if (payload.status === "signed_in") {
        return payload.usage_summary ? `Signed in. ${payload.usage_summary}` : "Signed in";
    }
    if (payload.status === "pending" && payload.user_code) {
        return `Enter ${payload.user_code} in the browser`;
    }
    return payload.status || "Not signed in";
}

function paint(node) {
    node.setDirtyCanvas?.(true, true);
    app.graph?.setDirtyCanvas(true, false);
}

function applyStatus(node, payload) {
    node._pymthouseText = statusText(payload);
    paint(node);
}

async function pollStatus(node) {
    if (node._pymthousePoll) {
        clearInterval(node._pymthousePoll);
    }
    node._pymthousePoll = setInterval(async () => {
        try {
            const response = await fetch(STATUS_PATH);
            const payload = await response.json();
            applyStatus(node, payload);
            if (payload.status === "signed_in" || payload.status === "error") {
                clearInterval(node._pymthousePoll);
                node._pymthousePoll = null;
                if (payload.status === "signed_in") {
                    showToast("success", "Signed in", payload.usage_summary || "PymtHouse session is ready");
                } else if (payload.message) {
                    showToast("error", "Sign in", payload.message);
                }
            }
        } catch (error) {
            console.error("PymtHouse status poll failed", error);
        }
    }, 2000);
}

async function signIn(node) {
    node._pymthouseText = "Starting sign-in…";
    paint(node);
    try {
        const response = await fetch(LOGIN_PATH, { method: "POST" });
        const payload = await response.json();
        applyStatus(node, payload);
        if (!response.ok) {
            showToast("error", "Sign in", payload.message || "Sign-in failed");
            return;
        }
        if (payload.status === "pending") {
            window.open(VERIFICATION_PATH, "_blank", "noopener,noreferrer");
        }
        if (payload.user_code) {
            showToast("info", "Approve sign-in", `Code ${payload.user_code}`, 8000);
        }
        pollStatus(node);
    } catch (error) {
        node._pymthouseText = "Sign-in failed";
        paint(node);
        showToast("error", "Sign in", "Could not reach the local sign-in route");
        console.error(error);
    }
}

app.registerExtension({
    name: "Livepeer.PymtHouseLogin",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "PymtHouseLogin") {
            return;
        }
        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);
            const node = this;
            node._pymthouseText = "Not signed in";
            const button = node.addWidget("button", "Sign in", "sign_in", () => {
                signIn(node);
            });
            if (button) {
                button.serialize = false;
            }
            const display = {
                name: "pymthouse_status",
                type: "customtext",
                value: "",
                options: { serialize: false },
                draw(ctx, drawnNode, width, y, height) {
                    const text = drawnNode._pymthouseText || "Not signed in";
                    ctx.fillStyle = "#1a1a2e";
                    ctx.fillRect(10, y, width - 20, height);
                    ctx.strokeStyle = "#4a9eff";
                    ctx.strokeRect(10, y, width - 20, height);
                    ctx.fillStyle = "#d7e6ff";
                    ctx.font = "12px sans-serif";
                    ctx.textBaseline = "middle";
                    const maxWidth = width - 30;
                    let shown = text;
                    while (shown.length > 3 && ctx.measureText(shown).width > maxWidth) {
                        shown = shown.slice(0, -2);
                    }
                    if (shown !== text) {
                        shown = `${shown.slice(0, -1)}…`;
                    }
                    ctx.fillText(shown, 15, y + height / 2);
                },
                computeSize() {
                    return [220, 28];
                },
            };
            if (!node.widgets) {
                node.widgets = [];
            }
            node.widgets.push(display);
            node.setSize?.(node.computeSize());
            return result;
        };
    },
});
