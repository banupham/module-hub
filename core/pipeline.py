from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from typing import Any

import requests

from adapters.http_adapters import TTSSpeakerAdapter, TranslateAdapter


class PipelineEngine:
    def __init__(self, module_manager):
        self.manager = module_manager
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

    def _sync_tts_language(self) -> None:
        if not self.config.get("tts"):
            return

        lang = str(
            self.config.get("tts_lang")
            or self.config.get("target_lang")
            or "vi"
        ).strip() or "vi"

        settings_url = self.manager.endpoint("tts_speaker", "/api/settings")
        response = requests.get(settings_url, timeout=4)
        response.raise_for_status()
        current = response.json()

        allowed = {
            "lang",
            "voice",
            "read_username",
            "chunk_chars",
            "normalize_abbreviations",
            "custom_replacements",
            "blocked_phrases",
        }
        payload = {key: value for key, value in current.items() if key in allowed}
        payload["lang"] = lang

        response = requests.post(settings_url, json=payload, timeout=4)
        response.raise_for_status()
        result = response.json()
        if not result.get("ok", True):
            raise RuntimeError(result.get("error") or "tts_language_sync_failed")

        self.log("config", f"TTS language: {lang}", tts_lang=lang)

    def start(self, config: dict[str, Any]) -> None:
        self.stop()
        self.config = dict(config)
        self._sync_tts_language()
        self.active = True
        self.stop_event.clear()
        self.last_stt_seq = 0
        self.log("pipeline", "Pipeline started", config=self.config)

        # V2.7 uses push by default: STT POSTs every newly-finalized sentence to
        # Hub /stt-event. Poll remains a fallback for old/external STT modules.
        if (
            self.config.get("source") == "stt"
            and str(self.config.get("stt_transport") or "push").lower() != "push"
        ):
            self.poll_thread = threading.Thread(target=self._poll_stt, daemon=True, name="stt-poller")
            self.poll_thread.start()

    def stop(self) -> None:
        self.active = False
        self.stop_event.set()
        thread = self.poll_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self.poll_thread = None

    def _wait_stt_ready(self) -> bool:
        while self.active and not self.stop_event.is_set():
            try:
                if self.manager.port("stt") is None:
                    self.stop_event.wait(0.2)
                    continue
                health = self.manager.health("stt", timeout=0.5)
                if health.get("online"):
                    return True
            except Exception:
                pass
            self.stop_event.wait(0.25)
        return False

    def _poll_stt(self) -> None:
        if not self._wait_stt_ready():
            return

        # Start at the current server cursor so a newly-started Hub session does
        # not replay sentences that existed before this pipeline started.
        try:
            state = requests.get(self.manager.endpoint("stt", "/health"), timeout=2).json()
            self.last_stt_seq = int(state.get("sentenceSeq") or 0)
        except Exception:
            self.last_stt_seq = 0

        while self.active and not self.stop_event.is_set():
            try:
                if self.manager.port("stt") is None:
                    if not self._wait_stt_ready():
                        return

                result_url = self.manager.endpoint("stt", "/api/result")
                response = requests.get(
                    result_url,
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
                event = self.stt_sentence_to_event(result)
                self.process_event(event)
            except Exception as exc:
                if self.active and not self.stop_event.is_set():
                    try:
                        health = self.manager.health("stt", timeout=0.4)
                    except Exception:
                        health = {"online": False}
                    if not health.get("online"):
                        if not self._wait_stt_ready():
                            return
                        continue
                    self.log("error", f"STT poll: {exc}")
                self.stop_event.wait(1.5)

    def stt_sentence_to_event(self, sentence: dict[str, Any]) -> dict[str, Any]:
        text = str(sentence.get("text") or "").strip()
        instance_id = str(sentence.get("instanceId") or "local")
        seq = int(sentence.get("seq") or 0)
        detected = sentence.get("detectedLang") or sentence.get("lang")
        return {
            "eventId": f"stt-{instance_id}-{seq or uuid.uuid4().hex[:8]}",
            "eventType": "comment",
            "user": {"id": "stt", "uniqueId": "stt", "displayName": "Google STT"},
            "payload": {
                "text": text,
                "source": "stt",
                "lang": sentence.get("lang"),
                "detectedLang": detected,
                "stt": sentence,
            },
        }

    def process_event(self, event: dict[str, Any]) -> dict[str, Any]:
        if not self.active:
            return {"ok": True, "accepted": True, "pipeline_active": False}

        event_type = str(event.get("eventType") or "")
        payload = event.get("payload") or {}
        text = str(payload.get("text") or "").strip()
        detected_lang = payload.get("detectedLang")
        suffix = f" [{detected_lang}]" if detected_lang else ""
        self.log("input", f"{event_type}{suffix}: {text or '(no text)'}", event=event)

        if event_type != "comment" or not text:
            return {"ok": True, "accepted": True, "ignored": True}

        output_text = text
        translation = None
        if self.config.get("translate"):
            translate_base = self.manager.base_url("translate")
            if not translate_base:
                raise RuntimeError("Translate module chưa có runtime endpoint")
            translation = self.translate_adapter.translate(
                translate_base,
                text,
                source=self.config.get("source_lang", "auto"),
                target=self.config.get("target_lang", "vi"),
                mode=self.config.get("translate_mode", "advanced"),
            )
            output_text = translation["translation"]
            self.log("translate", output_text, translation=translation)

        tts_result = None
        if self.config.get("tts"):
            event_url = self.manager.endpoint("tts_speaker", "/tiktok-event")
            tts_result = self.tts_adapter.speak_event(event_url, event, output_text)
            lang = self.config.get("tts_lang") or self.config.get("target_lang", "vi")
            self.log("output", f"TTS[{lang}] queued: {output_text}", result=tts_result)

        return {
            "ok": True,
            "accepted": True,
            "text": text,
            "detected_lang": detected_lang,
            "output_text": output_text,
            "translation": translation,
            "tts": tts_result,
        }
