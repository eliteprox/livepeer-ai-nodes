import { app } from "../../../scripts/app.js";

app.registerExtension({
    name: "Livepeer.Settings",
    settings: [
        {
            id: "Livepeer.auth_mode",
            name: "Login method",
            type: "combo",
            defaultValue: "device",
            options: [
                { text: "Device code (headless)", value: "device" },
                { text: "Browser popup (PKCE)",   value: "browser" },
            ],
            tooltip: "Device code: shows a code in the console to enter on a web page. Browser popup: opens a login page in your default browser.",
            category: ["Livepeer", "Authentication", "Login method"],
        },
    ],
});
