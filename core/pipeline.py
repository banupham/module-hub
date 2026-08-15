from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from typing import Any

import requests

from adapters.http_adapters import TTSSpeakerAdapter, TranslateAdapter


class PipelineEngine:
    def __init__(self):
        self.translate_adapter = TranslateAdapter()
        self.tts_adapter = TTSSpeakerAdapter()
        self.active = False
        self.config: dict[str, Any] = {}
        self.events: deque[dict[str, Any]] = deque(maxlen=200)
        self.lock = threading.RLock()
        self.poll_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.last_stt_seq = 0

    def log(self, kind: str, message: str, **extra: Any) -> None:
        item = {
            "ts": int(time.time() * 1000),
            "kind": kind,
            "message": message,
            **extra,
        }
        with self.lock:
            self.events.appendleft(item)

    def recent(self, limit: int = 80) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.events)[: max(1, min(limit, 200))]

    def start(self, config: dict[str, Any]) -> None:
        self.stop()
        self.config = dict(config)
        self.active = True
        self.stop_event.clear()
        self.log("pipeline", "Pipeline started", config=self.config)
        if self.config.get("source") == "stt":
            self.poll_thread = threading.Thread(target=self._poll_stt, daemon=True, name="stt-poller")
            self.poll_thread.start()

    def stop(self) -> None:
        self.active = False
        self.stop_event.set()
        thread = self.poll_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self.poll_thread = None

    def _poll_stt(self) -> None:
        while self.active and not self.stop_event.is_set():
            try:
                response = requests.get(
                    "http://127.0.0.1:8091/api/result",
                    params={"after": self.last_stt_seq, "timeout": 20},
                    timeout=25,
                )
                response.raise_for_status()
                data = response.json()
                result = data.get("result")
                if not result:
                    continue
                seq = int(result.get("seq") or 0)
                if seq <= self.last_stt_seq:
                    continue
                self.last_stt_seq = seq
                text = str(result.get("text") or "").strip()
                if not text:
                    continue
                event = {
                    "eventId": f"stt-{uuid.uuid4().hex[:12]}",
                    "eventType": "comment",
                    "user": {"id": "stt", "uniqueId": "stt", "displayName": "Google STT"},
                    "payload": {"text": text, "source": "stt", "stt": result},
                }
                self.process_event(event)
            except Exception as exc:
                self.log("error", f"STT poll: {exc}")
                self.stop_event.wait(1.5)

    def process_event(self, event: dict[str, Any]) -> dict[str, Any]:
        event_type = str(event.get("eventType") or "")
        text = str((event.get("payload") or {}).get("text") or "").strip()
        self.log("input", f"{event_type}: {text or '(no text)'}", event=event)

        if not self.active:
            return {"ok": True, "accepted": True, "pipeline_active": False}
        if event_type != "comment" or not text:
            return {"ok": True, "accepted": True, "ignored": True}

        output_text = text
        translation = None
        if self.config.get("translate"):
            translation = self.translate_adapter.translate(
                text,
                source=self.config.get("source_lang", "auto"),
                target=self.config.get("target_lang", "vi"),
                mode=self.config.get("translate_mode", "advanced"),
            )
            output_text = translation["translation"]
            self.log("translate", output_text, translation=translation)

        tts_result = None
        if self.config.get("tts"):
            tts_result = self.tts_adapter.speak_event(event, output_text)
            self.log("output", f"TTS queued: {output_text}", result=tts_result)

        return {
            "ok": True,
            "accepted": True,
            "text": text,
            "output_text": output_text,
            "translation": translation,
            "tts": tts_result,
        }
