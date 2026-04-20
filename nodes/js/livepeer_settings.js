import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

function renderLoginButton() {
    const container = document.createElement("div");
    container.style.display = "flex";
    container.style.flexDirection = "column";
    container.style.alignItems = "flex-end";
    container.style.gap = "6px";

    const button = document.createElement("button");
    button.type = "button";
    button.className = "p-button p-component p-button-sm";
    button.textContent = "Loading...";

    const status = document.createElement("div");
    status.style.fontSize = "12px";
    status.style.opacity = "0.8";
    status.style.maxWidth = "280px";
    status.style.textAlign = "right";
    status.textContent = "Checking Livepeer login status...";

    let isAuthenticated = false;
    let loginInProgress = false;

    const fetchAuthStatus = async () => {
        const response = await api.fetchApi("/livepeer/auth/status", { method: "GET" });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
            throw new Error(data.error || `HTTP ${response.status}`);
        }
        isAuthenticated = Boolean(data.authenticated);
        button.textContent = isAuthenticated ? "Logout" : "Login";
        status.textContent = isAuthenticated
            ? "Logged in. Click Logout to clear this device session."
            : "Not logged in. Click Login for device code auth.";
    };

    const setLoadingState = (busyLabel) => {
        button.disabled = true;
        button.textContent = busyLabel;
    };

    const clearLoadingState = () => {
        button.disabled = loginInProgress;
        button.textContent = isAuthenticated ? "Logout" : "Login";
    };

    const waitForLoginCompletion = async () => {
        for (let attempt = 0; attempt < 180; attempt += 1) {
            await new Promise((resolve) => setTimeout(resolve, 1000));
            try {
                const response = await api.fetchApi("/livepeer/auth/status", { method: "GET" });
                const data = await response.json().catch(() => ({}));
                if (!response.ok || !data.ok) {
                    throw new Error(data.error || `HTTP ${response.status}`);
                }

                if (data.authenticated) {
                    isAuthenticated = true;
                    loginInProgress = false;
                    status.textContent = "Login successful. You can start streaming now.";
                    clearLoadingState();
                    return;
                }

                if (data.login_error) {
                    loginInProgress = false;
                    status.textContent = `Login failed: ${data.login_error}`;
                    clearLoadingState();
                    return;
                }

                if (!data.login_in_progress) {
                    loginInProgress = false;
                    status.textContent = "Login did not complete. Click Login to try again.";
                    clearLoadingState();
                    return;
                }

                if (data.auth_url) {
                    const codeText = data.user_code ? ` Code: ${data.user_code}.` : "";
                    status.textContent = `Complete device login in browser.${codeText}`;
                }
            } catch (error) {
                const errorMessage = error instanceof Error ? error.message : String(error);
                loginInProgress = false;
                status.textContent = `Login status check failed: ${errorMessage}`;
                clearLoadingState();
                return;
            }
        }

        loginInProgress = false;
        status.textContent = "Login timed out. Click Login to try again.";
        clearLoadingState();
    };

    button.addEventListener("click", async () => {
        try {
            if (isAuthenticated) {
                setLoadingState("Logging out...");
                status.textContent = "Clearing saved Livepeer session...";
                const response = await api.fetchApi("/livepeer/auth/logout", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: "{}",
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok || !data.ok) {
                    throw new Error(data.error || `HTTP ${response.status}`);
                }
                isAuthenticated = false;
                status.textContent = "Logged out. Click Login to sign in again.";
            } else {
                setLoadingState("Logging in...");
                status.textContent = "Preparing browser login...";
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
                    isAuthenticated = true;
                    status.textContent = "Login successful. You can start streaming now.";
                } else if (data.pending && data.auth_url) {
                    loginInProgress = true;
                    window.open(data.auth_url, "_blank", "noopener,noreferrer");
                    const codeText = data.user_code ? ` Code: ${data.user_code}.` : "";
                    status.textContent = `Complete device login in browser tab.${codeText} Waiting for confirmation...`;
                    clearLoadingState();
                    waitForLoginCompletion();
                    return;
                } else {
                    throw new Error("Login could not be started.");
                }
            }
        } catch (error) {
            const errorMessage = error instanceof Error ? error.message : String(error);
            status.textContent = `${isAuthenticated ? "Logout" : "Login"} failed: ${errorMessage}`;
        } finally {
            clearLoadingState();
        }
    });

    button.disabled = true;
    fetchAuthStatus().catch((error) => {
        const errorMessage = error instanceof Error ? error.message : String(error);
        isAuthenticated = false;
        loginInProgress = false;
        button.textContent = "Login";
        status.textContent = `Status check failed: ${errorMessage}`;
    }).finally(() => {
        button.disabled = false;
    });

    container.appendChild(button);
    container.appendChild(status);
    return container;
}

app.registerExtension({
    name: "Livepeer.Settings",
    settings: [
        {
            id: "Livepeer.login_button",
            name: "Login",
            type: renderLoginButton,
            defaultValue: "",
            tooltip: "Start device-code login and open authorization page in your browser.",
            category: ["Livepeer", "Authentication", "Login"],
        },
    ],
});
