from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory

from core.module_manager import ModuleManager
from core.pipeline import PipelineEngine

ROOT = Path(__file__).resolve().parent
REGISTRY = ROOT / "modules" / "registry.json"
CONFIG_LOCAL = ROOT / "config.local.json"
PROFILES_DIR = ROOT / "profiles"

app = Flask(__name__, static_folder=None)
manager = ModuleManager(ROOT, REGISTRY, CONFIG_LOCAL)
pipeline = PipelineEngine()
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
        time.sleep(0.5)
    return False


def start_required_modules(config: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    if config.get("translate"):
        results.append({"module": "translate", **manager.start("translate")})
        wait_online("translate", 12)

    if config.get("tts"):
        results.append({"module": "tts_api", **manager.start("tts_api")})
        wait_online("tts_api", 12)
        results.append({"module": "tts_speaker", **manager.start("tts_speaker")})
        wait_online("tts_speaker", 12)

    source = config.get("source")
    if source == "stt":
        mode = str(config.get("stt_source") or "mic")
        if mode not in {"mic", "pc", "server"}:
            mode = "mic"
        results.append({"module": "stt", **manager.start("stt", mode=mode)})
        wait_online("stt", 15)
        if mode == "server":
            try:
                import requests
                requests.post(
                    "http://127.0.0.1:8091/api/start",
                    json={
                        "source": "mic",
                        "lang": config.get("stt_lang", "en-US"),
                        "continuous": True,
                        "interim": True,
                        "segmentation": True,
                        "sensitivity": config.get("stt_sensitivity", "balanced"),
                    },
                    timeout=5,
                ).raise_for_status()
            except Exception as exc:
                pipeline.log("error", f"STT start API: {exc}")
    elif source == "tiktok":
        username = str(config.get("tiktok_username") or "").strip()
        if not username:
            raise ValueError("Thiếu TikTok username")
        results.append({"module": "tiktok", **manager.start("tiktok", options={"username": username})})
        wait_online("tiktok", 10)

    return results


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
            "pid": None,
            "eventPath": "/tiktok-event",
            "hub": "module-hub",
            "version": "0.1.0",
        }
    )


@app.get("/api/health")
def api_health():
    return jsonify(
        {
            "ok": True,
            "service": "module-hub",
            "version": "0.1.0",
            "uptime_seconds": round(time.time() - STARTED_AT, 1),
            "pipeline_active": pipeline.active,
        }
    )


@app.get("/api/modules")
def api_modules():
    return jsonify(
        {
            "ok": True,
            "repo_paths": manager.config["repo_paths"],
            "modules": manager.all_health(),
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
        result = manager.start(
            module_id,
            mode=str(data.get("mode") or "default"),
            options=data.get("options") or {},
        )
        return jsonify(result)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/modules/<module_id>/stop")
def api_module_stop(module_id: str):
    try:
        return jsonify(manager.stop(module_id))
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
    try:
        started = start_required_modules(config) if auto_start else []
        pipeline.start(config)
        return jsonify({"ok": True, "active": True, "config": config, "started": started})
    except Exception as exc:
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
        return jsonify({"ok": False, "accepted": True, "error": str(exc)}), 200


if __name__ == "__main__":
    print("Module Hub: http://127.0.0.1:8899")
    app.run(host="127.0.0.1", port=8899, threaded=True, use_reloader=False)
