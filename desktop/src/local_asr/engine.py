"""GUI-independent adapter for streaming audio to the CapsWriter ASR server."""
from dataclasses import asdict, dataclass, field
from pathlib import Path
import base64
import json
import subprocess
import threading
import time
import uuid
import websocket
from .config import VENDOR, WS_URL

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class TranscriptionCancelled(Exception):
    pass


@dataclass
class TranscriptionResult:
    text: str
    duration: float
    elapsed: float
    tokens: list = field(default_factory=list)
    timestamps: list = field(default_factory=list)
    model: str = "Qwen3-ASR-1.7B-Q5_K"

    def to_dict(self):
        return asdict(self)


class ASRClient:
    """Transcribe without touching the GUI, clipboard, microphone or keyboard.

    The engine server must already be running (desktop app or --headless).
    `timestamps=False` skips forced alignment for low-latency dictation.
    """
    def __init__(self, url=WS_URL, ffmpeg=None, ffprobe=None, timeout=180, workspace=None):
        self.url = url
        vendor = Path(workspace) / "CapsWriter-Offline" if workspace else VENDOR
        self.ffmpeg = Path(ffmpeg) if ffmpeg else vendor / "ffmpeg.exe"
        self.ffprobe = Path(ffprobe) if ffprobe else vendor / "ffprobe.exe"
        self.timeout = timeout

    def duration(self, path):
        process = subprocess.run([str(self.ffprobe), "-v", "error", "-show_entries",
            "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, timeout=30, creationflags=NO_WINDOW)
        try:
            value = float(process.stdout.strip())
            return value if value > 0 else 0.0
        except ValueError:
            return 0.0

    def transcribe(self, path, *, language="auto", timestamps=True, progress=None, cancel=None):
        path = Path(path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"文件不存在：{path}")
        cancel = cancel or threading.Event()
        if cancel.is_set():
            raise TranscriptionCancelled()
        duration = self.duration(path)
        ws = websocket.create_connection(self.url, timeout=5, subprotocols=["binary"],
                                          http_no_proxy=["127.0.0.1", "localhost"])
        ws.settimeout(1)
        task_id = uuid.uuid4().hex
        started = time.monotonic()
        finished = threading.Event()
        send_done = threading.Event()
        final = []
        errors = []
        acknowledged = [0.0]
        last_message = [time.monotonic()]

        def report(value, stage):
            if progress:
                progress(max(0, min(0.99, value)), stage)

        def receive():
            try:
                while not finished.is_set() and not cancel.is_set():
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        if time.monotonic() - last_message[0] > self.timeout:
                            raise TimeoutError("识别响应超时，请检查模型服务")
                        continue
                    if not raw:
                        raise ConnectionError("识别服务已断开")
                    message = json.loads(raw)
                    if message.get("task_id") != task_id:
                        continue
                    last_message[0] = time.monotonic()
                    acknowledged[0] = float(message.get("duration", 0))
                    report(acknowledged[0] / duration if duration else 0.5, "识别中")
                    if message.get("is_final"):
                        final.append(message)
                        break
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()

        receiver = threading.Thread(target=receive, daemon=True)
        receiver.start()
        decoder = None
        sent = 0
        try:
            decoder = subprocess.Popen([str(self.ffmpeg), "-nostdin", "-v", "error", "-i", str(path),
                "-vn", "-f", "f32le", "-ac", "1", "-ar", "16000", "pipe:1"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
            while True:
                if cancel.is_set():
                    raise TranscriptionCancelled()
                if errors:
                    raise errors[0]
                # Keep at most ~3 minutes in flight, even for multi-hour recordings.
                while sent / 64000 - acknowledged[0] > 150:
                    if cancel.wait(0.1):
                        raise TranscriptionCancelled()
                    if errors:
                        raise errors[0]
                chunk = decoder.stdout.read(128000)
                if not chunk:
                    break
                if len(chunk) % 4:
                    raise ValueError("音频解码数据不完整")
                sent += len(chunk)
                message = dict(task_id=task_id, source="file" if timestamps else "mic",
                    data=base64.b64encode(chunk).decode("ascii"), is_final=False,
                    time_start=time.time(), seg_duration=30, seg_overlap=3,
                    context="", language=language)
                ws.send(json.dumps(message))
            if decoder.wait(timeout=15) != 0 or sent == 0:
                raise ValueError("无法读取音频，请选择包含声音的录音或视频文件")
            ws.send(json.dumps(dict(task_id=task_id, source="file" if timestamps else "mic", data="",
                is_final=True, time_start=time.time(), seg_duration=30, seg_overlap=3,
                context="", language=language)))
            send_done.set()
            while not finished.wait(0.1):
                if cancel.is_set():
                    raise TranscriptionCancelled()
            if cancel.is_set():
                raise TranscriptionCancelled()
            if errors:
                raise errors[0]
            if not final:
                raise RuntimeError("没有收到完整识别结果")
            message = final[0]
            text = message.get("text_accu") or message.get("text", "")
            real_duration = sent / 64000
            return TranscriptionResult(text=text, duration=round(real_duration, 3),
                elapsed=round(time.monotonic()-started, 3), tokens=message.get("tokens", []),
                timestamps=[min(real_duration, max(0, float(t))) for t in message.get("timestamps", [])])
        finally:
            finished.set()
            if decoder:
                if decoder.poll() is None:
                    decoder.terminate()
                    try:
                        decoder.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        decoder.kill()
                if decoder.stdout:
                    decoder.stdout.close()
            ws.close()
            receiver.join(timeout=2)
