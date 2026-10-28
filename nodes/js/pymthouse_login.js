import { app } from "../../../scripts/app.js";

const ACCOUNT_ROOT = "/livepeer-ai-nodes/account";
const LOGIN_PATH = `${ACCOUNT_ROOT}/login`;
const STATUS_PATH = `${ACCOUNT_ROOT}/status`;
const USAGE_PATH = `${ACCOUNT_ROOT}/usage`;
const LOGOUT_PATH = `${ACCOUNT_ROOT}/logout`;

let control;
let latestStatus = { status: "idle", signed_in: false, message: "Not signed in" };
let statusTimer;

function showToast(severity, summary, detail, life = 4000) {
    if (app.extensionManager?.toast?.add) {
        app.extensionManager.toast.add({ severity, summary, detail, life });
        return;
    }
    console[severity === "error" ? "error" : "info"](`${summary}: ${detail}`);
}

async function request(path, options = {}) {
    const response = await fetch(path, options);
    let payload = {};
    try {
        payload = await response.json();
    } catch {
        payload = { message: "The local account service returned an invalid response." };
    }
    if (!response.ok) {
        throw new Error(payload.message || "Account request failed.");
    }
    return payload;
}

function statusText(payload) {
    if (payload?.message) return payload.message;
    if (payload?.signed_in) return payload.usage_summary ? `Signed in · ${payload.usage_summary}` : "Signed in";
    if (payload?.status === "pending" && payload.user_code) return `Approve code ${payload.user_code}`;
    return "Not signed in";
}

function usageText(payload) {
    const data = { usage: payload?.usage || {}, balance: payload?.balance || {} };
    if (!Object.keys(data.usage).length && !Object.keys(data.balance).length) {
        return "Usage details appear here after you sign in.";
    }
    return JSON.stringify(data, null, 2);
}

function setBusy(busy) {
    control.signIn.disabled = busy;
    control.refresh.disabled = busy || !latestStatus.signed_in;
    control.signOut.disabled = busy || !latestStatus.signed_in;
}

function render(payload = latestStatus) {
    latestStatus = payload || latestStatus;
    const signedIn = Boolean(latestStatus.signed_in);
    const pending = latestStatus.status === "pending";
    control.status.textContent = statusText(latestStatus);
    control.details.textContent = usageText(latestStatus);
    control.userCode.textContent = pending && latestStatus.user_code ? `Code: ${latestStatus.user_code}` : "";
    control.refresh.hidden = !signedIn;
    control.signOut.hidden = !signedIn;
    control.signIn.hidden = signedIn || pending;
    control.openVerification.hidden = !pending || !latestStatus.verification_uri_complete;
    control.openVerification.href = latestStatus.verification_uri_complete || "#";
    control.badge.dataset.state = signedIn ? "signed-in" : pending ? "pending" : "idle";
    control.badge.textContent = signedIn ? "Connected" : pending ? "Awaiting approval" : "Not connected";
    setBusy(false);
}

function startPolling() {
    window.clearInterval(statusTimer);
    statusTimer = window.setInterval(async () => {
        try {
            const status = await request(STATUS_PATH);
            const becameSignedIn = !latestStatus.signed_in && status.signed_in;
            render(status);
            if (becameSignedIn) {
                showToast("success", "ComfyPeer connected", status.usage_summary || "Your stream account is ready.");
            }
            if (status.status !== "pending") {
                window.clearInterval(statusTimer);
                statusTimer = undefined;
            }
        } catch (error) {
            console.error("ComfyPeer account status poll failed", error);
        }
    }, 2000);
}

async function loadStatus() {
    try {
        render(await request(STATUS_PATH));
    } catch (error) {
        control.status.textContent = "Local account service unavailable";
        console.error(error);
    }
}

async function signIn() {
    setBusy(true);
    control.status.textContent = "Starting secure device sign-in…";
    try {
        const payload = await request(LOGIN_PATH, { method: "POST" });
        render(payload);
        if (payload.status === "pending") {
            const popup = window.open(payload.verification_uri_complete, "_blank", "noopener,noreferrer");
            if (!popup) {
                showToast("info", "Open device sign-in", "Use the Open secure sign-in link in the account panel.", 7000);
            }
            showToast("info", "Approve sign-in", `Enter code ${payload.user_code} in the browser.`, 8000);
            startPolling();
        }
    } catch (error) {
        setBusy(false);
        control.status.textContent = error.message || "Could not start sign-in.";
        showToast("error", "ComfyPeer sign-in", control.status.textContent, 7000);
        console.error(error);
    }
}

async function refreshUsage() {
    setBusy(true);
    try {
        render(await request(USAGE_PATH, { method: "POST" }));
        showToast("success", "Usage refreshed", latestStatus.usage_summary || "Account usage is up to date.");
    } catch (error) {
        setBusy(false);
        showToast("error", "Usage refresh", error.message || "Could not refresh usage.");
        console.error(error);
    }
}

async function signOut() {
    setBusy(true);
    try {
        window.clearInterval(statusTimer);
        statusTimer = undefined;
        render(await request(LOGOUT_PATH, { method: "POST" }));
        showToast("info", "ComfyPeer disconnected", "Local account credentials were cleared.");
    } catch (error) {
        setBusy(false);
        showToast("error", "Sign out", error.message || "Could not sign out.");
        console.error(error);
    }
}

function installControl() {
    if (document.getElementById("livepeer-comfypeer-account")) return;
    if (!document.body) {
        window.addEventListener("DOMContentLoaded", installControl, { once: true });
        return;
    }

    const style = document.createElement("style");
    style.textContent = `
        #livepeer-comfypeer-account { position: fixed; right: 16px; bottom: 16px; z-index: 10001; font: 13px/1.4 system-ui, sans-serif; }
        #livepeer-comfypeer-account button { border: 1px solid #4b5f87; border-radius: 7px; padding: 7px 10px; color: #edf3ff; background: #19233a; cursor: pointer; }
        #livepeer-comfypeer-account button:hover { background: #253556; }
        #livepeer-comfypeer-account button:disabled { cursor: wait; opacity: .6; }
        #livepeer-account-panel { width: 350px; margin-bottom: 8px; padding: 14px; color: #edf3ff; background: #111a2d; border: 1px solid #42577f; border-radius: 10px; box-shadow: 0 12px 32px #0008; }
        #livepeer-account-panel[hidden] { display: none; }
        #livepeer-account-panel h3 { margin: 0; font-size: 15px; }
        #livepeer-account-panel p { margin: 8px 0; color: #c8d5ef; }
        #livepeer-account-panel .lp-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
        #livepeer-account-panel .lp-spread { justify-content: space-between; }
        #livepeer-account-panel .lp-badge { padding: 2px 7px; border-radius: 999px; color: #dce9ff; background: #42506c; }
        #livepeer-account-panel .lp-badge[data-state="signed-in"] { background: #166534; }
        #livepeer-account-panel .lp-badge[data-state="pending"] { background: #a16207; }
        #livepeer-account-panel a { color: #9bc2ff; }
        #livepeer-account-panel pre { max-height: 145px; overflow: auto; padding: 8px; color: #c7d7f4; background: #0a1020; border-radius: 6px; white-space: pre-wrap; word-break: break-word; }
        #livepeer-account-panel .lp-muted { color: #9fb0cd; font-size: 12px; }
    `;
    document.head.append(style);

    const root = document.createElement("section");
    root.id = "livepeer-comfypeer-account";
    root.innerHTML = `
        <div id="livepeer-account-panel" role="dialog" aria-label="ComfyPeer account" hidden>
            <div class="lp-row lp-spread"><h3>ComfyPeer account</h3><span class="lp-badge" data-role="badge">Not connected</span></div>
            <p data-role="status">Not signed in</p>
            <p class="lp-muted" data-role="user-code"></p>
            <div class="lp-row">
                <button type="button" data-role="sign-in">Sign in</button>
                <a data-role="verification" target="_blank" rel="noopener noreferrer" hidden>Open secure sign-in</a>
                <button type="button" data-role="refresh" hidden>Refresh usage</button>
                <button type="button" data-role="sign-out" hidden>Sign out</button>
            </div>
            <p class="lp-muted">Usage and balance are read with your account JWT. Tokens are never shown or saved in the workflow.</p>
            <pre data-role="details">Usage details appear here after you sign in.</pre>
        </div>
        <button type="button" data-role="toggle" aria-expanded="false">ComfyPeer account</button>
    `;
    document.body.append(root);

    control = {
        panel: root.querySelector("#livepeer-account-panel"),
        toggle: root.querySelector('[data-role="toggle"]'),
        badge: root.querySelector('[data-role="badge"]'),
        status: root.querySelector('[data-role="status"]'),
        userCode: root.querySelector('[data-role="user-code"]'),
        signIn: root.querySelector('[data-role="sign-in"]'),
        openVerification: root.querySelector('[data-role="verification"]'),
        refresh: root.querySelector('[data-role="refresh"]'),
        signOut: root.querySelector('[data-role="sign-out"]'),
        details: root.querySelector('[data-role="details"]'),
    };
    control.toggle.addEventListener("click", () => {
        const open = control.panel.hidden;
        control.panel.hidden = !open;
        control.toggle.setAttribute("aria-expanded", String(open));
        if (open) loadStatus();
    });
    control.signIn.addEventListener("click", signIn);
    control.refresh.addEventListener("click", refreshUsage);
    control.signOut.addEventListener("click", signOut);
    loadStatus();
}

app.registerExtension({
    name: "Livepeer.ComfyPeerAccount",
    setup() {
        installControl();
    },
});
