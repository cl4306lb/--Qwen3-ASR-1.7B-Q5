"""Microphone capture and hotkeys. Imported only by the desktop UI."""
from array import array
import logging
from pathlib import Path
import threading
import time
import uuid
import wave
import keyboard
import sounddevice as sd
from .config import DATA, prepare_folders


class Microphone:
    def __init__(self):
        self._lock = threading.RLock()
        self._stream = None
        self._writer = None
        self.path = None
        self.started = 0
        self.level = 0.0
        self.error = ""

    def start(self):
        prepare_folders()
        info = sd.query_devices(kind="input")
        rate = int(info["default_samplerate"])
        self.path = DATA / "temp" / f"mic-{uuid.uuid4().hex}.wav"
        self._writer = wave.open(str(self.path), "wb")
        self._writer.setnchannels(1)
        self._writer.setsampwidth(2)
        self._writer.setframerate(rate)
        self.error = ""

        def callback(data, frames, timing, status):
            try:
                with self._lock:
                    if self._writer:
                        self._writer.writeframesraw(bytes(data))
                samples = array("h")
                samples.frombytes(bytes(data))
                self.level = min(1, max((abs(x) for x in samples), default=0) / 16000)
                if status:
                    logging.warning("Microphone status: %s", status)
            except Exception as exc:
                self.error = str(exc)

        try:
            self._stream = sd.RawInputStream(samplerate=rate, channels=1, dtype="int16",
                blocksize=max(128, int(rate * 0.05)), callback=callback)
            self._stream.start()
            self.started = time.monotonic()
        except Exception:
            self.stop()
            raise
        return info["name"]

    def stop(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            if self._writer:
                self._writer.close()
                self._writer = None
        self.level = 0
        return self.path


class CapsLockHotkey:
    def __init__(self, callback):
        self.callback = callback
        self.enabled = False
        self._pressed = False
        self._held = False
        self._timer = None
        self._lock = threading.RLock()
        self._remove = keyboard.hook_key("caps lock", self._event, suppress=True)

    def _event(self, event):
        with self._lock:
            if not self.enabled:
                return True
            if event.event_type == "down":
                if not self._pressed:
                    self._pressed, self._held = True, False
                    self._timer = threading.Timer(0.3, self._hold)
                    self._timer.daemon = True
                    self._timer.start()
            else:
                if not self._pressed:
                    return True
                if self._timer:
                    self._timer.cancel()
                self._pressed = False
                if self._held:
                    self.callback("stop_hotkey")
                else:
                    threading.Thread(target=keyboard.send, args=("caps lock",), daemon=True).start()
                self._held = False
            return False

    def _hold(self):
        with self._lock:
            if self._pressed and self.enabled:
                self._held = True
                self.callback("start_hotkey")

    def set_enabled(self, value):
        with self._lock:
            if not value and self._held:
                self.callback("stop_hotkey")
            if not value and self._timer:
                self._timer.cancel()
            self.enabled = bool(value)
            self._pressed = self._held = False

    def close(self):
        self.set_enabled(False)
        self._remove()
