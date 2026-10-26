import argparse
import ctypes
import hashlib
import logging
import logging.handlers
from pathlib import Path
import signal
import sys
import threading
import json
import urllib.request
from .api import APIServer
from .config import DATA, HTTP_PORT, ROOT, prepare_folders
from .service import SpeechService


def main():
    parser = argparse.ArgumentParser(description="Local speech assistant / HTTP service")
    parser.add_argument("--headless", action="store_true", help="Only run the HTTP interface, without microphone or GUI")
    parser.add_argument("--port", type=int, default=HTTP_PORT)
    parser.add_argument("files", nargs="*")
    args = parser.parse_args()
    prepare_folders()
    handler = logging.handlers.RotatingFileHandler(DATA / "logs" / "application.log",
        maxBytes=2*1024*1024, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    mutex = None
    if sys.platform == "win32":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.restype = ctypes.c_void_p
        name = "Local\\SpeechDesk-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]
        mutex = kernel.CreateMutexW(None, False, name)
        if ctypes.get_last_error() == 183:
            if args.files:
                try:
                    from .api import api_token
                    for path in args.files:
                        request = urllib.request.Request(f"http://127.0.0.1:{args.port}/v1/jobs",
                            data=json.dumps({"file_path": str(Path(path).resolve())}).encode(),
                            headers={"Authorization": "Bearer " + api_token(), "Content-Type": "application/json"})
                        with urllib.request.urlopen(request, timeout=10) as response:
                            response.read()
                except Exception as exc:
                    ctypes.windll.user32.MessageBoxW(None, str(exc), "无法提交文件", 0x10)
            if not args.headless:
                user = ctypes.windll.user32
                user.FindWindowW.restype = ctypes.c_void_p
                window = user.FindWindowW(None, "Speech Desk - 语音助手")
                if window:
                    user.ShowWindow(ctypes.c_void_p(window), 5)
                    user.SetForegroundWindow(ctypes.c_void_p(window))
                else:
                    user.MessageBoxW(None, "后台接口服务已在运行，请先结束它，再打开悬浮窗。", "语音助手", 0x40)
            return 0
    service, api = None, None
    try:
        service = SpeechService()
        api = APIServer(service, args.port)
        api.start()
        if args.headless:
            service.warm()
            stopped = threading.Event()
            service.shutdown_callback = stopped.set
            signal.signal(signal.SIGINT, lambda *unused: stopped.set())
            signal.signal(signal.SIGTERM, lambda *unused: stopped.set())
            if sys.stdout:
                print(f"Speech Desk API listening on http://127.0.0.1:{args.port}", flush=True)
            stopped.wait()
        else:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass
            from .ui import SpeechWindow
            window = SpeechWindow(service, api)
            service.shutdown_callback = lambda: window.events.put(("shutdown", None))
            if args.files:
                window.root.after(300, lambda: window.submit_files(args.files))
            window.run()
    except Exception as exc:
        logging.exception("Application failed")
        if not args.headless:
            ctypes.windll.user32.MessageBoxW(None, str(exc), "语音助手无法启动", 0x10)
        return 1
    finally:
        if api:
            api.close()
        if service:
            service.close(stop_shared=True)
        if mutex:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(mutex))
    return 0
