"""Lifecycle of the existing, separately packaged CapsWriter recognition engine."""
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import websocket
from .config import DATA, VENDOR, WS_URL, prepare_folders

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def matching_processes(executable):
    """Query exact executable paths, never kill processes based on a name alone."""
    escaped = str(Path(executable).resolve()).replace("'", "''")
    name = Path(executable).name.replace("'", "''")
    script = (f"@(Get-CimInstance Win32_Process -Filter \"name='{name}'\" | "
              f"Where-Object {{ $_.ExecutablePath -eq '{escaped}' }} | "
              "Select-Object ProcessId,ParentProcessId,CommandLine) | ConvertTo-Json -Compress")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                            capture_output=True, timeout=15, creationflags=NO_WINDOW)
    if result.returncode:
        raise RuntimeError("无法检查识别进程，请稍后重试")
    text = result.stdout.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    data = json.loads(text)
    return data if isinstance(data, list) else [data]


def stop_original_microphone_client():
    for process in matching_processes(VENDOR / "start_client.exe"):
        # File-transcription clients have a file argument; leave those running.
        command = process.get("CommandLine", "").strip()
        if command.startswith('"'):
            tail = command[command.find('"', 1) + 1:].strip()
        else:
            tail = command.partition(" ")[2].strip()
        if not tail:
            subprocess.run(["taskkill.exe", "/PID", str(process["ProcessId"]), "/T", "/F"],
                           capture_output=True, creationflags=NO_WINDOW, timeout=10)


class EngineProcess:
    def __init__(self):
        self._lock = threading.RLock()
        self._process = None
        self._log = None
        self.ready = False
        self.loading = False
        self.error = ""

    @staticmethod
    def probe():
        try:
            ws = websocket.create_connection(WS_URL, timeout=1, subprotocols=["binary"],
                                             http_no_proxy=["127.0.0.1", "localhost"])
            ws.close()
            return True
        except Exception:
            return False

    def ensure_started(self, timeout=180):
        with self._lock:
            if self.probe():
                self.ready = True
                self.error = ""
                return
            self.ready = False
            self.loading = True
            self.error = ""
            try:
                prepare_folders()
                executable = VENDOR / "start_server.exe"
                if not executable.is_file():
                    raise FileNotFoundError("找不到识别引擎，请将应用保留在安装目录中")
                if self._process is None or self._process.poll() is not None:
                    existing = matching_processes(executable)
                    if not existing:
                        environment = dict(os.environ)
                        environment["PATH"] = str(VENDOR) + os.pathsep + environment.get("PATH", "")
                        self._log = (DATA / "logs" / "engine.log").open("ab", buffering=0)
                        self._process = subprocess.Popen([str(executable)], cwd=VENDOR,
                            stdin=subprocess.DEVNULL, stdout=self._log, stderr=self._log,
                            env=environment, creationflags=NO_WINDOW)
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if self.probe():
                        self.ready = True
                        return
                    if self._process and self._process.poll() is not None:
                        raise RuntimeError("模型启动失败，详情见 desktop/data/logs/engine.log")
                    time.sleep(0.5)
                raise TimeoutError("模型载入超时，请查看引擎日志或重新启动")
            except Exception as exc:
                self.error = str(exc)
                raise
            finally:
                self.loading = False

    def stop(self, include_existing=False):
        with self._lock:
            if include_existing:
                processes = matching_processes(VENDOR / "start_server.exe")
                ids = {item["ProcessId"] for item in processes}
                roots = [item["ProcessId"] for item in processes if item["ParentProcessId"] not in ids]
            else:
                roots = [self._process.pid] if self._process and self._process.poll() is None else []
            for pid in roots:
                subprocess.run(["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, timeout=15, creationflags=NO_WINDOW)
            self._process = None
            self.ready = False
            if self._log:
                self._log.close()
                self._log = None
