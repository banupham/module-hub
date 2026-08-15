from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import requests

from core.ports import PortAllocator


class ModuleManager:
    def __init__(self, root: Path, registry_path: Path, config_path: Path):
        self.root = root
        self.registry_path = registry_path
        self.config_path = config_path
        raw_registry = json.loads(registry_path.read_text(encoding="utf-8"))
        self.hub_version = raw_registry.get("hub_version", "0.0.0")
        self.registry = raw_registry["modules"]
        self.processes: dict[str, subprocess.Popen] = {}
        self.logs: dict[str, Any] = {}
        self.runtime_origin: dict[str, str] = {}
        self.lock = threading.RLock()
        self.ports = PortAllocator("127.0.0.1")
        self.config = self._load_config()

    def _default_paths(self) -> dict[str, str]:
        parent = self.root.parent
        return {
            "tiktok": str(parent / "tiktok_live_cmd_active_viewers_v9"),
            "stt": str(parent / "google-stt-bridge"),
            "translate": str(parent / "google-translate-rpc"),
            "tts": str(parent / "google_loa_tts"),
        }

    def _load_config(self) -> dict[str, Any]:
        defaults = {"repo_paths": self._default_paths()}
        if not self.config_path.exists():
            return defaults
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            defaults["repo_paths"].update(data.get("repo_paths", {}))
            return defaults
        except Exception:
            return defaults

    def save_paths(self, repo_paths: dict[str, str]) -> None:
        with self.lock:
            for key, value in repo_paths.items():
                if key in self.config["repo_paths"] and isinstance(value, str):
                    self.config["repo_paths"][key] = value.strip()
            self.config_path.write_text(
                json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    def _cwd(self, module_id: str) -> Path:
        item = self.registry[module_id]
        base = Path(self.config["repo_paths"][item["repo_key"]]).expanduser()
        suffix = item.get("cwd_suffix")
        return base / suffix if suffix else base

    def _format_command(self, module_id: str, mode: str, options: dict[str, Any]) -> list[str]:
        item = self.registry[module_id]
        variants = item.get("start", {})
        command = variants.get(mode) or variants.get("default")
        if not command:
            raise ValueError(f"Module {module_id} không có lệnh start mode={mode}")
        formatted = []
        for part in command:
            try:
                formatted.append(str(part).format(**options))
            except KeyError as exc:
                raise ValueError(f"Thiếu tham số {exc.args[0]} cho {module_id}") from exc
        return formatted

    def port(self, module_id: str) -> int | None:
        return self.ports.get(module_id)

    def base_url(self, module_id: str) -> str | None:
        port = self.port(module_id)
        if port is None:
            return None
        return f"http://127.0.0.1:{port}"

    def endpoint(self, module_id: str, path: str = "") -> str:
        base = self.base_url(module_id)
        if not base:
            raise RuntimeError(f"Module {module_id} chưa được cấp/kết nối port")
        if not path:
            return base
        return base + (path if path.startswith("/") else f"/{path}")

    def attach(self, module_id: str, port: int) -> dict[str, Any]:
        if module_id not in self.registry:
            raise KeyError(module_id)
        with self.lock:
            process = self.processes.get(module_id)
            if process and process.poll() is None:
                raise RuntimeError(f"{module_id} đang do Hub quản lý; hãy stop trước khi attach")
            self.ports.attach(module_id, int(port))
            self.runtime_origin[module_id] = "external"
        return self.health(module_id)

    def detach(self, module_id: str) -> dict[str, Any]:
        with self.lock:
            process = self.processes.get(module_id)
            if process and process.poll() is None:
                raise RuntimeError(f"{module_id} đang chạy; hãy stop module thay vì detach")
            self.ports.release(module_id)
            self.runtime_origin.pop(module_id, None)
        return {"ok": True, "module": module_id, "detached": True}

    def start(
        self,
        module_id: str,
        mode: str = "default",
        options: dict[str, Any] | None = None,
        requested_port: int | None = None,
        extra_env: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        options = options or {}
        extra_env = extra_env or {}
        with self.lock:
            if module_id not in self.registry:
                raise KeyError(module_id)

            existing = self.processes.get(module_id)
            if existing and existing.poll() is None:
                return {
                    "ok": True,
                    "already_running": True,
                    "pid": existing.pid,
                    "port": self.port(module_id),
                    "base_url": self.base_url(module_id),
                }

            # Clear stale managed runtime state before allocating a new port.
            if existing and existing.poll() is not None:
                self.processes.pop(module_id, None)
                handle = self.logs.pop(module_id, None)
                if handle:
                    try:
                        handle.close()
                    except Exception:
                        pass
                self.ports.release(module_id)
                self.runtime_origin.pop(module_id, None)

            # An externally attached endpoint must be detached first; otherwise a
            # new process could accidentally target an occupied external port.
            if self.runtime_origin.get(module_id) == "external":
                raise RuntimeError(f"{module_id} đang attach tới port {self.port(module_id)}")

            cwd = self._cwd(module_id)
            if not cwd.exists():
                raise FileNotFoundError(f"Không thấy thư mục module: {cwd}")

            port = self.ports.allocate(module_id, requested=requested_port)
            item = self.registry[module_id]
            command = self._format_command(module_id, mode, options)
            env = os.environ.copy()
            env.update({str(k): str(v) for k, v in item.get("env", {}).items()})
            port_env = item.get("port_env")
            if port_env:
                env[str(port_env)] = str(port)
            env.update({str(k): str(v) for k, v in extra_env.items()})
            env.update({str(k): str(v) for k, v in options.get("env", {}).items()})

            log_dir = self.root / "logs"
            log_dir.mkdir(exist_ok=True)
            log_handle = open(
                log_dir / f"{module_id}.log",
                "a",
                encoding="utf-8",
                errors="replace",
            )

            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

            try:
                process = subprocess.Popen(
                    command,
                    cwd=str(cwd),
                    env=env,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
            except Exception:
                log_handle.close()
                self.ports.release(module_id)
                raise

            self.processes[module_id] = process
            self.logs[module_id] = log_handle
            self.runtime_origin[module_id] = "managed"
            return {
                "ok": True,
                "pid": process.pid,
                "port": port,
                "base_url": self.base_url(module_id),
                "health_url": self.endpoint(module_id, item.get("health_path", "/health")),
                "command": command,
                "cwd": str(cwd),
            }

    def stop(self, module_id: str) -> dict[str, Any]:
        with self.lock:
            process = self.processes.get(module_id)
            if not process or process.poll() is not None:
                # Do not kill or silently detach externally managed processes.
                return {
                    "ok": True,
                    "already_stopped": True,
                    "attached_external": self.runtime_origin.get(module_id) == "external",
                    "port": self.port(module_id),
                }

            pid = process.pid
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=8,
                    )
                else:
                    process.terminate()
                    process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

            self.processes.pop(module_id, None)
            handle = self.logs.pop(module_id, None)
            if handle:
                try:
                    handle.close()
                except Exception:
                    pass
            old_port = self.port(module_id)
            self.ports.release(module_id)
            self.runtime_origin.pop(module_id, None)
            return {"ok": True, "pid": pid, "released_port": old_port}

    def health(self, module_id: str, timeout: float = 1.2) -> dict[str, Any]:
        item = self.registry[module_id]
        port = self.port(module_id)
        health_path = item.get("health_path", "/health")
        url = self.endpoint(module_id, health_path) if port else None
        started = time.perf_counter()
        online = False
        detail: Any = "port_not_assigned"

        if url:
            try:
                response = requests.get(url, timeout=timeout)
                online = 200 <= response.status_code < 300
                try:
                    detail = response.json()
                except Exception:
                    detail = response.text[:200]
            except Exception as exc:
                detail = str(exc)

        elapsed = round((time.perf_counter() - started) * 1000, 1)
        process = self.processes.get(module_id)
        managed = bool(process and process.poll() is None)
        return {
            "id": module_id,
            "name": item["name"],
            "version": item.get("version"),
            "role": item.get("role"),
            "repo": item.get("repo"),
            "cwd": str(self._cwd(module_id)),
            "port": port,
            "base_url": self.base_url(module_id),
            "health_url": url,
            "online": online,
            "latency_ms": elapsed,
            "managed": managed,
            "origin": self.runtime_origin.get(module_id),
            "pid": process.pid if managed else None,
            "detail": detail,
        }

    def all_health(self) -> list[dict[str, Any]]:
        return [self.health(module_id) for module_id in self.registry]
