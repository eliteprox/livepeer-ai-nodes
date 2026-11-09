import { app } from "../../../scripts/app.js";

const APPS_PATH = "/livepeer-ai-nodes/discovery/capabilities";

function choiceFor(catalog, value) {
    const selected = (value || "").trim();
    const choices = Array.isArray(catalog?.choices) ? catalog.choices : [];
    return choices.find((item) => item.id === selected || item.label === selected || item.app === selected) || null;
}

function readFields(box) {
    const values = {};
    for (const input of box.querySelectorAll("[data-field]")) {
        const name = input.dataset.field;
        const kind = input.dataset.kind;
        if (input.type === "checkbox") values[name] = input.checked;
        else if (kind === "integer") values[name] = input.value === "" ? null : Number.parseInt(input.value, 10);
        else if (kind === "number") values[name] = input.value === "" ? null : Number(input.value);
        else values[name] = input.value;
    }
    return values;
}

function audioChoice(choice) {
    const type = choice?.call?.content_type || "";
    const framing = choice?.call?.framing || [];
    return type.includes("multipart") || (type.includes("json") && framing.includes("binary"));
}

function renderFields(box, choice, saved, message) {
    box.replaceChildren();
    const fields = (Array.isArray(choice?.fields) ? choice.fields : [])
        .filter((field) => !(box.dataset.audio === "1" && field.name === "file"));
    if (!fields.length) {
        const note = document.createElement("div");
        note.textContent = choice ? "This capability has no request fields." : (message || "Pick a capability.");
        note.style.cssText = "color:#9aabc8;font-size:12px;white-space:pre-wrap;";
        box.append(note);
        return;
    }
    for (const field of fields) {
        const row = document.createElement("label");
        row.style.cssText = "display:flex;flex-direction:column;gap:2px;color:#edf3ff;font-size:12px;";
        row.textContent = field.name;
        let input;
        if (Array.isArray(field.enum)) {
            input = document.createElement("select");
            for (const option of field.enum) {
                const item = document.createElement("option");
                item.value = String(option);
                item.textContent = String(option);
                input.append(item);
            }
        } else if (field.type === "boolean") {
            input = document.createElement("input");
            input.type = "checkbox";
        } else {
            input = document.createElement("input");
            input.type = field.type === "integer" || field.type === "number" ? "number" : "text";
            if (field.multiline) input = document.createElement("textarea");
        }
        input.dataset.field = field.name;
        input.dataset.kind = field.type;
        if (field.description) input.title = field.description;
        if (field.minimum != null) input.min = String(field.minimum);
        if (field.maximum != null) input.max = String(field.maximum);
        let initial = saved?.[field.name] ?? field.default;
        if (box.dataset.audio === "1" && field.name === "model" && !saved?.model?.trim?.()) {
            initial = choice.model?.name || initial;
        }
        if (input.type === "checkbox") input.checked = Boolean(initial);
        else if (initial != null) input.value = typeof initial === "string" ? initial : JSON.stringify(initial).replace(/^"|"$/g, "");
        input.style.cssText = "width:100%;box-sizing:border-box;padding:4px 6px;color:#edf3ff;background:#0a1020;border:1px solid #4b5f87;border-radius:6px;";
        if (field.multiline) input.rows = 3;
        const save = () => {
            box.dataset.value = box.widget.value;
            box.widget.callback?.(box.widget.value);
        };
        input.addEventListener("input", save);
        input.addEventListener("change", save);
        row.append(input);
        box.append(row);
    }
}

function appPicker(node, inputName, inputData) {
    const wrapper = document.createElement("div");
    wrapper.style.cssText = "display:flex;gap:4px;width:100%;align-items:center;";
    const input = document.createElement("select");
    input.style.cssText = "flex:1;min-width:0;box-sizing:border-box;padding:4px 6px;color:#edf3ff;background:#0a1020;border:1px solid #4b5f87;border-radius:6px;";
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.textContent = "Reload";
    refresh.title = "Reload capabilities from discovery";
    refresh.style.cssText = "padding:4px 8px;color:#edf3ff;background:#19233a;border:1px solid #4b5f87;border-radius:6px;cursor:pointer;";
    wrapper.append(input, refresh);

    let selected = "";
    const widget = node.addDOMWidget(inputName, "LIVEPEER_APP", wrapper, {
        getValue() { return selected; },
        setValue(value) {
            selected = value == null ? "" : String(value).trim();
            fillSelect(node.livepeerCatalog);
            showFields();
        },
    });

    function fillSelect(catalog) {
        input.replaceChildren();
        const blank = document.createElement("option");
        blank.value = "";
        const audioOnly = Boolean(node.constructor?.livepeerAudio);
        const choices = (catalog?.choices || []).filter((choice) => (
            audioOnly ? audioChoice(choice) : !audioChoice(choice)
        ));
        blank.textContent = choices.length ? "Select a capability" : (catalog?.message || "No capabilities");
        input.append(blank);
        for (const choice of choices) {
            const option = document.createElement("option");
            option.value = choice.label;
            option.textContent = choice.label;
            option.title = choice.summary || choice.call?.path || "";
            input.append(option);
        }
        if ([...input.options].some((option) => option.value === selected)) input.value = selected;
    }

    async function load() {
        const response = await fetch(APPS_PATH);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            node.livepeerCatalog = { choices: [], message: payload.message || `Discovery failed (${response.status})` };
        } else if (!Array.isArray(payload.choices)) {
            node.livepeerCatalog = { choices: [], message: "Restart ComfyUI so it serves capability descriptions." };
        } else {
            node.livepeerCatalog = payload;
        }
        fillSelect(node.livepeerCatalog);
        widget.callback?.(widget.value);
        showFields();
    }

    function showFields() {
        const box = node.livepeerFields;
        if (!box) return;
        let saved = {};
        try { saved = JSON.parse(box.dataset.value || "{}"); } catch { saved = {}; }
        const catalog = node.livepeerCatalog;
        const choice = choiceFor(catalog, selected);
        const message = choice?.message || (catalog && !catalog.choices?.length ? catalog.message : "");
        renderFields(box, choice, saved, message);
        box.widget?.callback?.(box.widget.value);
    }

    fillSelect(null);
    input.addEventListener("change", () => {
        const box = node.livepeerFields;
        if (box?.dataset.audio === "1" && input.value !== selected) {
            const values = readFields(box);
            delete values.model;
            delete values.voice;
            box.dataset.value = JSON.stringify(values);
        }
        selected = input.value.trim();
        widget.callback?.(widget.value);
        showFields();
    });
    refresh.addEventListener("pointerdown", (event) => { event.preventDefault(); event.stopPropagation(); });
    refresh.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        load().catch((error) => console.error("Discovery capability list failed", error));
    });
    node.livepeerShowFields = showFields;
    load().catch((error) => console.error("Discovery capability list failed", error));
    return { widget };
}

function fieldsWidget(node, inputName) {
    const box = document.createElement("div");
    box.style.cssText = "display:flex;flex-direction:column;gap:6px;width:100%;";
    const widget = node.addDOMWidget(inputName, "LIVEPEER_FIELDS", box, {
        getValue() {
            return box.querySelector("[data-field]") ? JSON.stringify(readFields(box)) : box.dataset.value || "{}";
        },
        setValue(value) {
            box.dataset.value = value == null ? "{}" : String(value);
            node.livepeerShowFields?.();
        },
    });
    box.widget = widget;
    if (node.constructor?.livepeerAudio) box.dataset.audio = "1";
    node.livepeerFields = box;
    node.livepeerShowFields?.();
    return { widget };
}

app.registerExtension({
    name: "Livepeer.DiscoveredApp",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "LivepeerRemoteFrames" && nodeData.name !== "LivepeerRemoteAudio") return;
        nodeType.livepeerAudio = nodeData.name === "LivepeerRemoteAudio";
        const appSpec = nodeData.input?.required?.app;
        if (appSpec) nodeData.input.required.app = ["LIVEPEER_APP", appSpec[1] || {}];
        const fields = nodeData.input?.optional?.fields;
        if (fields) nodeData.input.optional.fields = ["LIVEPEER_FIELDS", fields[1] || {}];
    },
    getCustomWidgets() {
        return { LIVEPEER_APP: appPicker, LIVEPEER_FIELDS: fieldsWidget };
    },
});
