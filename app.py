from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import requests
from flask import Flask, jsonify, request, send_from_directory

from core.module_manager import ModuleManager
from core.pipeline import PipelineEngine

ROOT = Path(__file__).resolve().parent
REGISTRY = ROOT / "modules" / "registry.json"
CONFIG_LOCAL = ROOT / "config.local.json"
PROFILES_DIR = ROOT / "profiles"

HUB_HOST = os.getenv("HUB_HOST", "127.0.0.1")
HUB_PORT = int(os.getenv("HUB_PORT", "8899"))
HUB_CALLBACK_HOST = os.getenv("HUB_CALLBACK_HOST", "127.0.0.1")

app = Flask(__name__, static_folder=None)
manager = ModuleManager(ROOT, REGISTRY, CONFIG_LOCAL)
pipeline = PipelineEngine(manager)
INSTANCE_ID = uuid.uuid4().hex[:12]
STARTED_AT = time.time()


def load_profiles() -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for path in sorted(PROFILES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["_file"] = path.name
            profiles.append(data)
        except Exception:
            continue
    return profiles


def wait_online(module_id: str, seconds: float = 12.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if manager.health(module_id, timeout=0.8)["online"]:
            return True
        time.sleep(0.4)
    return False


def ensure_started(
    module_id: str,
    *,
    mode: str = "default",
    options: dict[str, Any] | None = None,
    extra_env: dict[str, Any] | None = None,
    requested_port: int | None = None,
    wait_seconds: float = 12.0,
) -> dict[str, Any]:
    current = manager.health(module_id, timeout=0.5)
    if current["online"]:
        return {
            "ok": True,
            "module": module_id,
            "already_online": True,
            "port": current["port"],
            "base_url": current["base_url"],
            "origin": current["origin"],
        }

    result = manager.start(
        module_id,
        mode=mode,
        options=options,
        requested_port=requested_port,
        extra_env=extra_env,
    )
    online = wait_online(module_id, wait_seconds)
    result.update({"module": module_id, "online": online})
    if not online:
        result["warning"] = "process_started_but_health_not_ready"
    return result


def start_stt(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep the STT HTTP server owned by Hub; Chrome is only a helper process."""
    results: list[dict[str, Any]] = []
    source_mode = str(config.get("stt_source") or "mic")
    if source_mode not in {"mic", "pc", "server"}:
        source_mode = "mic"

    # START_SERVER_ONLY blocks and remains owned by ModuleManager, so its dynamic
    # port can be stopped/released reliably. The Chrome launcher sees this server
    # already online and therefore does not create a second orphaned server.
    results.append(ensure_started("stt", mode="server", wait_seconds=15))

    if source_mode in {"mic", "pc"}:
        helper = manager.launch_helper("stt", source_mode)
        helper["module"] = "stt-client"
        results.append(helper)
    else:
        start_url = manager.endpoint("stt", "/api/start")
        response = requests.post(
            start_url,
            json={
                "source": "mic",
                "lang": config.get("stt_lang", "en-US"),
                "continuous": True,
                "interim": True,
                "segmentation": True,
                "sensitivity": config.get("stt_sensitivity", "balanced"),
            },
            timeout=5,
        )
        response.raise_for_status()

    return results


def start_dependencies(config: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    if config.get("translate"):
        results.append(ensure_started("translate", wait_seconds=12))

    if config.get("tts"):
        results.append(ensure_started("tts_api", wait_seconds=12))
        tts_api_url = manager.endpoint("tts_api")
        speaker_env = {
            "LOA_TTS_API_URL": f"{tts_api_url}/tts",
            "LOA_TTS_HEALTH_URL": f"{tts_api_url}/health",
        }
        results.append(
            ensure_started(
                "tts_speaker",
                extra_env=speaker_env,
                wait_seconds=12,
            )
        )

    return results


def start_source(config: dict[str, Any]) -> list[dict[str, Any]]:
    source = config.get("source")

    if source == "stt":
        return start_stt(config)

    if source == "tiktok":
        username = str(config.get("tiktok_username") or "").strip()
        if not username:
            raise ValueError("Thiếu TikTok username")
        callback = f"http://{HUB_CALLBACK_HOST}:{HUB_PORT}/tiktok-event"
        return [
            ensure_started(
                "tiktok",
                options={"username": username},
                extra_env={"WEBHOOK_URLS": callback},
                wait_seconds=10,
            )
        ]

    raise ValueError(f"Nguồn pipeline không hợp lệ: {source}")


def validate_connected_modules(config: dict[str, Any]) -> None:
    required: list[str] = []
    if config.get("translate"):
        required.append("translate")
    if config.get("tts"):
        required.extend(["tts_api", "tts_speaker"])
    if config.get("source") == "stt":
        required.append("stt")
    elif config.get("source") == "tiktok":
        required.append("tiktok")

    missing = [module_id for module_id in required if not manager.health(module_id, 0.6)["online"]]
    if missing:
        raise RuntimeError("Module chưa online: " + ", ".join(missing))


@app.get("/")
def index():
    return send_from_directory(ROOT / "web", "index.html")


@app.get("/web/<path:name>")
def web_asset(name: str):
    return send_from_directory(ROOT / "web", name)


@app.get("/health")
def middleware_health():
    return jsonify(
        {
            "ok": True,
            "service": "game-event-server",
            "instanceId": INSTANCE_ID,
            "instanceToken": INSTANCE_ID,
            "pid": os.getpid(),
            "eventPath": "/tiktok-event",
            "hub": "module-hub",
            "version": manager.hub_version,
            "port": HUB_PORT,
        }
    )


@app.get("/api/health")
def api_health():
    return jsonify(
        {
            "ok": True,
            "service": "module-hub",
            "version": manager.hub_version,
            "host": HUB_HOST,
            "port": HUB_PORT,
            "uptime_seconds": round(time.time() - STARTED_AT, 1),
            "pipeline_active": pipeline.active,
            "allocated_ports": manager.ports.snapshot(),
        }
    )


@app.get("/api/modules")
def api_modules():
    return jsonify(
        {
            "ok": True,
            "repo_paths": manager.config["repo_paths"],
            "modules": manager.all_health(),
            "allocated_ports": manager.ports.snapshot(),
        }
    )


@app.post("/api/config/paths")
def api_save_paths():
    data = request.get_json(silent=True) or {}
    manager.save_paths(data.get("repo_paths", {}))
    return jsonify({"ok": True, "repo_paths": manager.config["repo_paths"]})


@app.post("/api/modules/<module_id>/start")
def api_module_start(module_id: str):
    data = request.get_json(silent=True) or {}
    try:
        requested_port = data.get("port")
        if requested_port in ("", None, 0, "0"):
            requested_port = None
        requested_port = int(requested_port) if requested_port is not None else None
        mode = str(data.get("mode") or "default")

        if module_id == "stt" and mode in {"mic", "pc"}:
            base = ensure_started("stt", mode="server", requested_port=requested_port, wait_seconds=15)
            helper = manager.launch_helper("stt", mode)
            return jsonify({"ok": True, "server": base, "client": helper})

        extra_env = data.get("env") or {}
        if module_id == "tts_speaker" and manager.base_url("tts_api"):
            tts_base = manager.base_url("tts_api")
            extra_env = {
                "LOA_TTS_API_URL": f"{tts_base}/tts",
                "LOA_TTS_HEALTH_URL": f"{tts_base}/health",
                **extra_env,
            }

        result = manager.start(
            module_id,
            mode=mode,
            options=data.get("options") or {},
            requested_port=requested_port,
            extra_env=extra_env,
        )
        return jsonify(result)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/modules/<module_id>/stop")
def api_module_stop(module_id: str):
    try:
        current = manager.health(module_id, timeout=0.3)
        if module_id == "stt" and current.get("managed") and current.get("port"):
            try:
                requests.post(manager.endpoint("stt", "/api/release"), json={}, timeout=2)
            except Exception:
                pass
            try:
                manager.launch_helper("stt", "close")
            except Exception:
                pass
        return jsonify(manager.stop(module_id))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/modules/<module_id>/connect")
def api_module_connect(module_id: str):
    data = request.get_json(silent=True) or {}
    try:
        port = int(data.get("port"))
        return jsonify({"ok": True, **manager.attach(module_id, port)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/modules/<module_id>/disconnect")
def api_module_disconnect(module_id: str):
    try:
        return jsonify(manager.detach(module_id))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.get("/api/profiles")
def api_profiles():
    return jsonify({"ok": True, "profiles": load_profiles()})


@app.get("/api/pipeline")
def api_pipeline_state():
    return jsonify({"ok": True, "active": pipeline.active, "config": pipeline.config})


@app.post("/api/pipeline/start")
def api_pipeline_start():
    data = request.get_json(silent=True) or {}
    config = data.get("config") or data
    auto_start = bool(data.get("auto_start", True))
    pipeline.stop()
    try:
        started: list[dict[str, Any]] = []
        if auto_start:
            started.extend(start_dependencies(config))
            pipeline.start(config)
            started.extend(start_source(config))
        else:
            validate_connected_modules(config)
            pipeline.start(config)
        return jsonify(
            {
                "ok": True,
                "active": True,
                "config": config,
                "started": started,
                "allocated_ports": manager.ports.snapshot(),
            }
        )
    except Exception as exc:
        pipeline.stop()
        pipeline.log("error", f"Pipeline start: {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/pipeline/stop")
def api_pipeline_stop():
    pipeline.stop()
    return jsonify({"ok": True, "active": False})


@app.get("/api/events")
def api_events():
    try:
        limit = int(request.args.get("limit", 80))
    except ValueError:
        limit = 80
    return jsonify({"ok": True, "events": pipeline.recent(limit)})


@app.post("/tiktok-event")
def tiktok_event():
    event = request.get_json(silent=True) or {}
    try:
        result = pipeline.process_event(event)
        return jsonify(result)
    except Exception as exc:
        pipeline.log("error", f"TikTok event: {exc}", event=event)
        # Middleware should receive 2xx so a downstream TTS/Translate failure does
        # not cause an event retry storm.
        return jsonify({"ok": False, "accepted": True, "error": str(exc)}), 200


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Module Hub local orchestrator")
    parser.add_argument("--host", default=HUB_HOST)
    parser.add_argument("--port", type=int, default=HUB_PORT)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    HUB_HOST = args.host
    HUB_PORT = args.port
    print(f"Module Hub: http://127.0.0.1:{HUB_PORT}")
    print("Module ports: dynamic, allocated by Hub at runtime")
    app.run(host=HUB_HOST, port=HUB_PORT, threaded=True, use_reloader=False)
