import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

app.registerExtension({
    name: "livepeer.session.recorder",
    nodeCreated(node) {
        if (node.comfyClass !== "StartSessionRecorder") return;
        const box = document.createElement("div");
        box.style.cssText = "display:flex;flex-direction:column;background:#171717;color:#ddd;overflow:hidden;height:100%;";
        const image = document.createElement("img");
        image.alt = "Live remote session preview";
        image.style.cssText = "width:100%;min-height:0;flex:1;object-fit:contain;";
        const status = document.createElement("div");
        status.textContent = "Waiting for remote frames…";
        status.style.cssText = "font:12px sans-serif;padding:6px;";
        box.append(image, status);
        node.addDOMWidget("session_preview", "preview", box, { serialize: false });
        node.setSize([Math.max(node.size[0], 320), Math.max(node.size[1], 440)]);
        const listener = ({ detail }) => {
            if (String(node.id) !== String(detail.node_id)) return;
            image.src = `data:image/jpeg;base64,${detail.image}`;
            status.textContent = `${detail.seen} received · ${detail.captured} captured` +
                (detail.omitted ? ` · capture limit reached (${detail.omitted} omitted)` : "");
        };
        api.addEventListener("livepeer-recording-preview", listener);
        const removed = node.onRemoved;
        node.onRemoved = function () {
            api.removeEventListener("livepeer-recording-preview", listener);
            return removed?.apply(this, arguments);
        };
    },
});
