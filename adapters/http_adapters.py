from __future__ import annotations

import copy
import uuid
from typing import Any

import requests


class TranslateAdapter:
    def translate(
        self,
        base_url: str,
        text: str,
        source: str = "auto",
        target: str = "vi",
        mode: str = "advanced",
    ) -> dict[str, Any]:
        response = requests.post(
            f"{base_url.rstrip('/')}/translate",
            json={"text": text, "from": source, "to": target, "mode": mode},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("error") or "translate_failed")
        return data


class TTSSpeakerAdapter:
    def speak_event(self, event_url: str, event: dict[str, Any], text: str) -> dict[str, Any]:
        forwarded = copy.deepcopy(event)
        forwarded["eventId"] = f"hub-{uuid.uuid4().hex[:12]}"
        forwarded["eventType"] = "comment"
        forwarded.setdefault("user", {})
        forwarded["user"].setdefault("id", "module_hub")
        forwarded["user"].setdefault("uniqueId", "module_hub")
        forwarded["user"].setdefault("displayName", "Module Hub")
        forwarded.setdefault("payload", {})["text"] = text
        forwarded["payload"]["hubForwarded"] = True
        response = requests.post(event_url, json=forwarded, timeout=10)
        response.raise_for_status()
        try:
            return response.json()
        except Exception:
            return {"ok": True, "status": response.status_code}
