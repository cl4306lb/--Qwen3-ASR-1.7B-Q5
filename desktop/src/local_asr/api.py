"""Loopback-only HTTP API. Both desktop and --headless use the same implementation."""
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import secrets
import socket
import threading
import time
from urllib.parse import unquote, urlsplit
import uuid
from .config import DATA, HTTP_PORT, prepare_folders
from .jobs import TERMINAL

MAX_UPLOAD = 512 * 1024 * 1024


def api_token():
    prepare_folders()
    path = DATA / "api-token.txt"
    if not path.exists():
        # Exclusive creation also avoids races between simultaneous launches.
        try:
            with path.open("x", encoding="ascii") as stream:
                stream.write(secrets.token_urlsafe(32))
        except FileExistsError:
            pass
    return path.read_text(encoding="ascii").strip()


class APIServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, service, port=HTTP_PORT):
        self.service = service
        self.token = api_token()
        self.allowed_origins = {x.strip() for x in os.environ.get("SPEECH_DESK_ORIGINS", "").split(",") if x.strip()}
        super().__init__(("127.0.0.1", port), APIHandler)
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.serve_forever, daemon=True, name="SpeechHTTP")
        self.thread.start()

    def close(self):
        if self.thread:
            self.shutdown()
            self.thread.join(timeout=3)
        self.server_close()


class APIHandler(BaseHTTPRequestHandler):
    server_version = "SpeechDesk/1.0"

    def log_message(self, format, *args):
        logging.getLogger("api").info(format, *args)

    def _reply(self, status, value, *, content_type="application/json; charset=utf-8"):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8") if not isinstance(value, bytes) else value
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        origin = self.headers.get("Origin")
        if origin in self.server.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Filename")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _authorize(self):
        expected = "Bearer " + self.server.token
        supplied = self.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("ascii")):
            self._reply(401, {"error": "需要有效的本地接口令牌", "code": "unauthorized"})
            return False
        return True

    def _guard(self, callback):
        try:
            callback()
        except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
            self._reply(400, {"error": str(exc), "code": "invalid_request"})
        except KeyError as exc:
            self._reply(404, {"error": str(exc), "code": "not_found"})
        except socket.timeout:
            self._reply(408, {"error": "上传超时", "code": "upload_timeout"})
        except RuntimeError as exc:
            self._reply(409, {"error": str(exc), "code": "service_unavailable"})
        except Exception:
            logging.exception("HTTP request failed")
            self._reply(500, {"error": "服务处理失败，请查看日志", "code": "internal_error"})

    def do_OPTIONS(self):
        if self.headers.get("Origin") not in self.server.allowed_origins:
            return self._reply(403, {"error": "未允许此网页来源"})
        self._reply(200, {"ok": True})

    def do_GET(self):
        def run():
            route = urlsplit(self.path).path.rstrip("/")
            if route == "/health":
                return self._reply(200, self.server.service.health())
            if not self._authorize():
                return
            if route == "/v1/jobs":
                return self._reply(200, {"jobs": self.server.service.jobs.list_jobs(30)})
            pieces = route.split("/")
            if len(pieces) == 4 and pieces[1:3] == ["v1", "jobs"]:
                return self._reply(200, self.server.service.jobs.get(pieces[3]))
            if len(pieces) == 6 and pieces[1:3] == ["v1", "jobs"] and pieces[4] == "files":
                job = self.server.service.jobs.get(pieces[3])
                formats = {"text": ("文字.txt", "text/plain; charset=utf-8"),
                           "json": ("结果.json", "application/json; charset=utf-8"),
                           "srt": ("字幕.srt", "text/plain; charset=utf-8")}
                if pieces[5] not in formats or not job["output_dir"]:
                    raise KeyError("尚无此输出文件")
                filename, content_type = formats[pieces[5]]
                path = Path(job["output_dir"]) / filename
                if not path.is_file():
                    raise KeyError("尚无此输出文件")
                return self._reply(200, path.read_bytes(), content_type=content_type)
            self._reply(404, {"error": "接口不存在", "code": "not_found"})
        self._guard(run)

    def _read_submission(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_UPLOAD:
            raise ValueError("请求内容为空或超过 512MB；大文件请使用 file_path 方式")
        self.connection.settimeout(30)
        temporary = False
        path = None
        try:
            if "application/json" in self.headers.get("Content-Type", ""):
                if length > 65536:
                    raise ValueError("JSON 请求过大")
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError("请求内容不完整")
                payload = json.loads(body)
                if not isinstance(payload, dict) or not isinstance(payload.get("file_path"), str):
                    raise ValueError("请提供 file_path 字符串")
                path = Path(payload["file_path"])
                if not path.is_absolute():
                    raise ValueError("file_path 必须是本机文件的绝对路径")
                language = payload.get("language", "auto")
                timestamps = payload.get("timestamps", True)
                if not isinstance(language, str) or len(language) > 32 or not isinstance(timestamps, bool):
                    raise ValueError("language 必须是字符串，timestamps 必须为布尔值")
                name = path.name
            else:
                name = Path(unquote(self.headers.get("X-Filename", "upload.wav"))).name
                suffix = Path(name).suffix[:10] or ".wav"
                path = DATA / "temp" / f"upload-{uuid.uuid4().hex}{suffix}"
                temporary = True
                remaining = length
                with path.open("wb") as stream:
                    while remaining:
                        chunk = self.rfile.read(min(65536, remaining))
                        if not chunk:
                            raise ValueError("上传内容不完整")
                        stream.write(chunk)
                        remaining -= len(chunk)
                language, timestamps = "auto", True
            return self.server.service.submit(path, language=language, timestamps=timestamps,
                                               name=name, temporary=temporary)
        except Exception:
            if temporary and path and path.is_relative_to(DATA / "temp"):
                path.unlink(missing_ok=True)
            raise
        finally:
            self.connection.settimeout(None)

    def do_POST(self):
        def run():
            if not self._authorize():
                return
            route = urlsplit(self.path).path.rstrip("/")
            if route == "/v1/shutdown":
                if any(job["status"] not in TERMINAL for job in self.server.service.jobs.list_jobs(1000)):
                    return self._reply(409, {"error": "请先等待或取消识别任务，再退出"})
                if not self.server.service.shutdown_callback:
                    return self._reply(409, {"error": "此服务没有配置退出处理器"})
                self._reply(200, {"ok": True})
                threading.Timer(0.1, self.server.service.shutdown_callback).start()
                return
            if route not in {"/v1/jobs", "/v1/transcribe"}:
                return self._reply(404, {"error": "接口不存在"})
            job = self._read_submission()
            if route == "/v1/jobs":
                return self._reply(202, job)
            deadline = time.monotonic() + 3600
            while time.monotonic() < deadline:
                job = self.server.service.jobs.get(job["id"])
                if job["status"] in TERMINAL:
                    return self._reply(200 if job["status"] == "succeeded" else 422, job)
                time.sleep(0.1)
            self._reply(202, job)
        self._guard(run)

    def do_DELETE(self):
        def run():
            if not self._authorize():
                return
            pieces = urlsplit(self.path).path.rstrip("/").split("/")
            if len(pieces) == 4 and pieces[1:3] == ["v1", "jobs"]:
                return self._reply(200, self.server.service.jobs.cancel(pieces[3]))
            self._reply(404, {"error": "接口不存在"})
        self._guard(run)
