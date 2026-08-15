from __future__ import annotations

import socket
import threading
from typing import Any


class PortAllocator:
    """Allocate local TCP ports for Hub-managed modules.

    Ports are selected by asking the OS for an available ephemeral port, then the
    child module is started immediately with that port. A requested port can also
    be supplied when the user wants a deterministic/manual connection.
    """

    def __init__(self, host: str = "127.0.0.1"):
        self.host = host
        self._lock = threading.RLock()
        self._ports: dict[str, int] = {}

    def _is_bindable(self, port: int) -> bool:
        if not (1 <= int(port) <= 65535):
            return False
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            sock.bind((self.host, int(port)))
            return True
        except OSError:
            return False
        finally:
            sock.close()

    def _os_free_port(self) -> int:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((self.host, 0))
            return int(sock.getsockname()[1])
        finally:
            sock.close()

    def allocate(self, module_id: str, requested: int | None = None) -> int:
        with self._lock:
            existing = self._ports.get(module_id)
            if existing:
                return existing

            if requested is not None:
                requested = int(requested)
                if not self._is_bindable(requested):
                    raise RuntimeError(f"Port {requested} không khả dụng")
                port = requested
            else:
                port = self._os_free_port()
                for _ in range(20):
                    if port not in self._ports.values() and self._is_bindable(port):
                        break
                    port = self._os_free_port()
                else:
                    raise RuntimeError("Không tìm được TCP port trống")

            if port in self._ports.values():
                raise RuntimeError(f"Port {port} đã được Hub cấp cho module khác")

            self._ports[module_id] = port
            return port

    def attach(self, module_id: str, port: int) -> int:
        """Remember an externally started module port without requiring it to be free."""
        port = int(port)
        if not (1 <= port <= 65535):
            raise ValueError("Port phải nằm trong 1..65535")
        with self._lock:
            owner = next((key for key, value in self._ports.items() if value == port and key != module_id), None)
            if owner:
                raise RuntimeError(f"Port {port} đã được gán cho {owner}")
            self._ports[module_id] = port
        return port

    def get(self, module_id: str) -> int | None:
        with self._lock:
            return self._ports.get(module_id)

    def release(self, module_id: str) -> None:
        with self._lock:
            self._ports.pop(module_id, None)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._ports)
