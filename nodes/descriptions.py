"""Call descriptions for runners on the network.

A description is built from a runner's ``runner_contract: 1`` envelope and the
pinned registry entry its endpoint names. Forms and callers use that document.
They do not read a runner's own prose.
"""
from __future__ import annotations

import json
from functools import lru_cache

from livepeer_runner_contract import Registry
from livepeer_runner_contract.registry import EntryKind
from livepeer_runner_contract.select import parse_discovery

# Draft registry that publishes the comfystream profiles. Not a release tag.
REGISTRY_COMMIT = "3669351ce45be2c4b55236a61e641597cec79689"
_MULTILINE = {"prompt", "lyrics", "negative_prompt", "text"}


@lru_cache(maxsize=1)
def load_registry() -> Registry:
    return Registry.load()


def describe_discovery(payload) -> dict:
    """Choices a client can offer, plus why other runners were left out."""
    found = parse_discovery(payload)
    registry = load_registry()
    grouped: dict[tuple, dict] = {}
    for runner in found.runners:
        model = runner.envelope.model
        for endpoint in runner.envelope.endpoints.values():
            entry = registry.get(endpoint.profile)
            if entry is None or entry.kind is not EntryKind.PROFILE:
                continue
            operation = entry.operations.get(endpoint.name)
            if operation is None:
                continue
            capability = _capability(entry, runner.app)
            identity = (
                entry.id,
                operation.name,
                model.source if model is not None else runner.app,
            )
            choice = grouped.get(identity)
            if choice is None:
                content_type, schema = _request(operation)
                call_path = endpoint.path or operation.path
                channels = None
                if endpoint.channels is not None:
                    channels = {"in": list(endpoint.channels.inputs), "out": list(endpoint.channels.outputs)}
                summary = ""
                if registry.capabilities and capability in registry.capabilities:
                    summary = registry.capabilities[capability].summary
                choice = {
                    "id": ":".join(identity),
                    "label": _label(capability, model, operation.name),
                    "app": runner.app,
                    "capability": capability,
                    "summary": summary,
                    "mode": entry.mode.value if entry.mode is not None else runner.mode,
                    "profile": entry.id,
                    "endpoint": operation.name,
                    "model": None if model is None else {
                        "name": model.name, "source": model.source, "revision": model.revision,
                    },
                    "fields": _fields(schema),
                    "schema": schema,
                    "call": {
                        "method": endpoint.method or operation.method,
                        "path": call_path,
                        "content_type": content_type,
                        "framing": [item.value for item in operation.framings],
                        "channels": channels,
                        "stop": endpoint.stop,
                        "idle_timeout_s": endpoint.idle_timeout_s,
                        "drain_timeout_s": endpoint.drain_timeout_s,
                        "inputs": list(operation.x.get("inputs") or []),
                        "outputs": list(operation.x.get("outputs") or []),
                    },
                    "runners": [],
                }
                grouped[identity] = choice
            choice["runners"].append(_runner(runner))
    choices = sorted(grouped.values(), key=lambda item: item["label"])
    return {
        "registry": {"commit": REGISTRY_COMMIT, "release": registry.release},
        "message": "",
        "apps": [item["label"] for item in choices],
        "choices": choices,
        "ignored": [
            {"orchestrator": item.key.orchestrator, "runner_id": item.key.runner_id, "reason": str(item.reason)}
            for item in found.ignored
        ],
    }


_SESSION_OPERATIONS = ("stream", "start", "session")


def describe_offerings(offerings, discovery) -> dict:
    """One choice per offering app. Fields come from a runner's metadata; the pool is every runner."""
    found = parse_discovery(discovery)
    registry = load_registry()
    by_app: dict[str, list] = {}
    for runner in found.runners:
        by_app.setdefault(runner.app, []).append(runner)
    grouped: dict[str, dict] = {}
    if isinstance(offerings, list):
        for item in offerings:
            if not isinstance(item, dict) or not isinstance(item.get("app"), str) or not item["app"].strip():
                continue
            app = item["app"].strip()
            slot = grouped.setdefault(app, {
                "modes": [], "rates": [], "stale": False, "rate_status": "available",
                "ready_count": 0, "runner_count": 0,
            })
            slot["modes"].extend(mode for mode in item.get("modes") or [] if isinstance(mode, str))
            slot["rates"].append({"low": item.get("rate_low"), "high": item.get("rate_high")})
            slot["stale"] = slot["stale"] or bool(item.get("stale"))
            if item.get("rate_status") == "missing":
                slot["rate_status"] = "missing"
            slot["ready_count"] = max(slot["ready_count"], item.get("ready_count") or 0)
            slot["runner_count"] = max(slot["runner_count"], item.get("runner_count") or 0)
    choices = []
    for app, offer in sorted(grouped.items()):
        persistent = "persistent" in offer["modes"]
        described = None
        for runner in by_app.get(app, []):
            described = _describe_runner(runner, registry, persistent)
            if described is not None:
                break
        message = "" if described else "No runner_contract metadata for this app."
        choices.append({
            "id": app,
            "label": app,
            "app": app,
            "capability": described["capability"] if described else app,
            "summary": _offering_summary(offer, described),
            "mode": (described or {}).get("mode") or (offer["modes"][0] if offer["modes"] else ""),
            "stale": offer["stale"],
            "rate_status": offer["rate_status"],
            "rates": offer["rates"],
            "ready_count": offer["ready_count"],
            "runner_count": offer["runner_count"],
            "profile": (described or {}).get("profile"),
            "endpoint": (described or {}).get("endpoint"),
            "model": (described or {}).get("model"),
            "fields": (described or {}).get("fields") or [],
            "schema": (described or {}).get("schema"),
            "call": (described or {}).get("call"),
            "message": message,
            "runners": [_runner(runner) for runner in by_app.get(app, [])],
        })
    return {
        "registry": {"commit": REGISTRY_COMMIT, "release": registry.release},
        "message": "",
        "apps": [item["label"] for item in choices],
        "choices": choices,
        "ignored": [
            {"orchestrator": item.key.orchestrator, "runner_id": item.key.runner_id, "reason": str(item.reason)}
            for item in found.ignored
        ],
    }


def _describe_runner(runner, registry, persistent: bool):
    picked = _pick_operation(runner, registry, persistent)
    if picked is None:
        return None
    endpoint, entry, operation = picked
    content_type, schema = _request(operation)
    channels = None
    if endpoint.channels is not None:
        channels = {"in": list(endpoint.channels.inputs), "out": list(endpoint.channels.outputs)}
    capability = _capability(entry, runner.app)
    summary = ""
    if registry.capabilities and capability in registry.capabilities:
        summary = registry.capabilities[capability].summary
    model = runner.envelope.model
    return {
        "capability": capability,
        "summary": summary,
        "mode": entry.mode.value if entry.mode is not None else runner.mode,
        "profile": entry.id,
        "endpoint": operation.name,
        "model": None if model is None else {
            "name": model.name, "source": model.source, "revision": model.revision,
        },
        "fields": _fields(schema),
        "schema": schema,
        "call": {
            "method": endpoint.method or operation.method,
            "path": endpoint.path or operation.path,
            "content_type": content_type,
            "framing": [item.value for item in operation.framings],
            "channels": channels,
            "stop": endpoint.stop,
            "idle_timeout_s": endpoint.idle_timeout_s,
            "drain_timeout_s": endpoint.drain_timeout_s,
            "inputs": list(operation.x.get("inputs") or []),
            "outputs": list(operation.x.get("outputs") or []),
        },
    }


def _pick_operation(runner, registry, persistent: bool):
    resolved = []
    for endpoint in runner.envelope.endpoints.values():
        entry = registry.get(endpoint.profile)
        if entry is None or entry.kind is not EntryKind.PROFILE:
            continue
        operation = entry.operations.get(endpoint.name)
        if operation is None:
            continue
        resolved.append((endpoint, entry, operation))
    if not resolved:
        return None
    if persistent:
        for name in _SESSION_OPERATIONS:
            for item in resolved:
                if item[0].name == name:
                    return item
    return resolved[0]


def _offering_summary(offer: dict, described) -> str:
    parts = []
    modes = sorted(set(offer["modes"]))
    if modes:
        parts.append(", ".join(modes))
    low = (offer["rates"][0].get("low") or {}) if offer["rates"] else {}
    if isinstance(low, dict) and low.get("price_usd") and low.get("unit"):
        parts.append(f"{low['price_usd']} USD/{low['unit']}")
    if described and described.get("summary"):
        parts.append(described["summary"])
    if offer["stale"]:
        parts.append("stale")
    if offer["rate_status"] == "missing":
        parts.append("rate missing")
    return " · ".join(parts)


def find_choice(catalog: dict, selected: str) -> dict | None:
    """Match a picker value to a choice by id, label, or advertised app."""
    selected = selected.strip()
    if not selected:
        return None
    exact = [item for item in catalog.get("choices") or []
             if selected in (item["id"], item["label"], item["app"])]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        labeled = [item for item in exact if item["label"] == selected or item["id"] == selected]
        return labeled[0] if len(labeled) == 1 else None
    return None


def build_request(choice: dict, values: dict, *, prompt: str = "", seconds: int = 5, timeout: int = 60) -> dict:
    """The JSON body for this choice. An open schema keeps the node's clip controls."""
    specs = {field["name"]: field for field in choice.get("fields") or []}
    if not specs:
        return {"prompt": prompt, "max_startup_s": timeout, "idle_timeout_s": max(10, int(seconds) + 5)}
    body = {}
    for name, spec in specs.items():
        if name in values and values[name] not in ("", None):
            body[name] = _coerce(values[name], spec)
        elif "default" in spec:
            body[name] = spec["default"]
    model = (choice.get("model") or {}).get("name")
    if "model" in specs and not body.get("model") and model:
        body["model"] = model
    missing = [name for name, spec in specs.items() if spec.get("required") and name not in body]
    if missing:
        raise ValueError("Missing " + ", ".join(missing))
    return body


def _capability(entry, app: str) -> str:
    if app in entry.capabilities:
        return app
    return entry.capabilities[0] if entry.capabilities else app


def _label(capability: str, model, endpoint: str) -> str:
    who = model.name if model is not None else endpoint.replace("_", " ")
    return f"{capability} · {who}"


def _runner(runner) -> dict:
    price = None
    if runner.price is not None:
        price = {
            "price": runner.price.price, "currency": runner.price.currency,
            "unit": runner.price.unit, "price_usd": runner.price.price_usd,
        }
    return {
        "orchestrator": runner.orchestrator,
        "url": runner.url,
        "app": runner.app,
        "runner_id": runner.runner_id,
        "mode": runner.mode,
        "capacity_available": runner.capacity_available,
        "price": price,
    }


def _request(operation):
    content = ((operation.spec.get("requestBody") or {}).get("content") or {})
    if not content:
        return None, None
    content_type = "application/json" if "application/json" in content else next(iter(content))
    schema = _resolve((content[content_type] or {}).get("schema") or {}, operation.document, ())
    return content_type, schema if isinstance(schema, dict) else None


def _resolve(node, document, seen: tuple):
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        if ref in seen:
            return {"type": "object"}
        target = document
        for part in ref[2:].split("/"):
            if not isinstance(target, dict):
                return {"type": "object"}
            target = target.get(part.replace("~1", "/").replace("~0", "~"))
        return _resolve(target, document, seen + (ref,))
    if isinstance(node.get("allOf"), list):
        merged = {"type": "object", "properties": {}, "required": []}
        for part in node["allOf"]:
            resolved = _resolve(part, document, seen)
            if not isinstance(resolved, dict):
                continue
            merged["properties"].update(resolved.get("properties") or {})
            merged["required"].extend(resolved.get("required") or [])
        return merged
    return node


def _fields(schema) -> list:
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict) or not properties:
        return []
    required = set(schema.get("required") or [])
    fields = []
    for name, spec in properties.items():
        if not isinstance(spec, dict):
            continue
        kind = spec.get("type")
        if isinstance(kind, list):
            kind = next((item for item in kind if item != "null"), kind[0] if kind else "string")
        if kind not in {"string", "integer", "number", "boolean"}:
            kind = "json"
        field = {"name": name, "type": kind, "required": name in required}
        if spec.get("description"):
            field["description"] = spec["description"]
        if "default" in spec:
            field["default"] = spec["default"]
        if isinstance(spec.get("enum"), list):
            field["enum"] = spec["enum"]
        for bound in ("minimum", "maximum"):
            if isinstance(spec.get(bound), int | float) and not isinstance(spec.get(bound), bool):
                field[bound] = spec[bound]
        if kind == "string" and (name in _MULTILINE or (spec.get("maxLength") or 0) > 160):
            field["multiline"] = True
        fields.append(field)
    return fields


def _coerce(value, spec: dict):
    kind = spec.get("type")
    if kind == "integer" and not isinstance(value, bool):
        return int(value)
    if kind == "number" and not isinstance(value, bool):
        return float(value)
    if kind == "boolean":
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes"}
        return bool(value)
    if kind == "json" and isinstance(value, str):
        text = value.strip()
        if not text:
            return value
        try:
            return json.loads(text)
        except ValueError:
            return value
    return value


def empty_catalog(message: str) -> dict:
    return {"registry": {"commit": REGISTRY_COMMIT, "release": None}, "message": message,
            "apps": [], "choices": [], "ignored": []}
