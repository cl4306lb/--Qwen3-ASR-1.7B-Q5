"""Persistent job queue; one file worker and one interactive worker."""
import json
from pathlib import Path
import queue
import re
import sqlite3
import threading
import time
import uuid
from .config import DATA, OUTPUTS, prepare_folders
from .engine import ASRClient, TranscriptionCancelled

TERMINAL = {"succeeded", "failed", "cancelled"}


def subtitle_time(value):
    milliseconds = max(0, round(value * 1000))
    seconds, ms = divmod(milliseconds, 1000)
    minutes, second = divmod(seconds, 60)
    hour, minute = divmod(minutes, 60)
    return f"{hour:02}:{minute:02}:{second:02},{ms:03}"


def make_subtitles(result):
    tokens = result.tokens
    stamps = result.timestamps
    if not tokens or len(tokens) != len(stamps):
        return ""
    groups, text, start = [], "", 0
    previous_end = 0.0
    for index, token in enumerate(tokens):
        if not text:
            start = index
        text += token.replace("@@", "")
        if re.search(r"[。！？!?；;]$", text) or len(text) >= 30 or index == len(tokens)-1:
            begin = max(previous_end, min(result.duration, stamps[start]))
            end = stamps[index+1] if index+1 < len(stamps) else result.duration
            end = min(result.duration, max(begin + 0.02, end))
            if end > begin and text.strip():
                groups.append(f"{len(groups)+1}\n{subtitle_time(begin)} --> {subtitle_time(end)}\n{text.strip()}\n")
                previous_end = end
            text = ""
    return "\n".join(groups)


class JobManager:
    def __init__(self, engine_process):
        prepare_folders()
        self.engine_process = engine_process
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(DATA / "jobs.sqlite3", check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        self._connection.commit()
        self._events = {}
        self._queues = {"file": queue.Queue(maxsize=32), "mic": queue.Queue(maxsize=4)}
        self._closing = threading.Event()
        self._workers = []
        for job in self.list_jobs(10000):
            if job["status"] not in TERMINAL:
                self._update(job["id"], status="failed", stage="已中断", error="程序上次退出时任务未完成，请重新提交")
        for kind in self._queues:
            worker = threading.Thread(target=self._run, args=(kind,), daemon=True, name=f"ASR-{kind}")
            worker.start()
            self._workers.append(worker)

    def _save(self, item):
        with self._lock:
            self._connection.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?)",
                (item["id"], json.dumps(item, ensure_ascii=False)))
            self._connection.commit()

    def _update(self, job_id, **changes):
        with self._lock:
            item = self.get(job_id)
            item.update(changes)
            self._save(item)
            return item

    def get(self, job_id):
        with self._lock:
            row = self._connection.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError("找不到此转录任务")
        return json.loads(row[0])

    def list_jobs(self, limit=30):
        with self._lock:
            rows = self._connection.execute("SELECT payload FROM jobs ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def submit(self, path, *, kind="file", language="auto", timestamps=True, name=None, temporary=False):
        path = Path(path).resolve()
        if not path.is_file():
            raise FileNotFoundError("找不到所选音视频文件")
        if self._closing.is_set():
            raise RuntimeError("服务正在关闭")
        if kind not in self._queues:
            raise ValueError("未知任务类型")
        with self._lock:
            if self._queues[kind].full():
                raise RuntimeError("等待任务过多，请稍后再试")
            job_id = uuid.uuid4().hex
            item = dict(id=job_id, name=name or path.name, kind=kind,
                status="queued", stage="等待识别", progress=0, created_at=time.time(),
                input_path=str(path), output_dir=None, result=None, error="")
            self._events[job_id] = threading.Event()
            self._save(item)
            self._queues[kind].put((job_id, language, timestamps, temporary))
            return item

    def cancel(self, job_id):
        with self._lock:
            item = self.get(job_id)
            if item["status"] in TERMINAL:
                return item
            self._events[job_id].set()
            return self._update(job_id, status="cancelled" if item["status"] == "queued" else "cancelling",
                                stage="已取消" if item["status"] == "queued" else "正在取消")

    def _run(self, kind):
        while not self._closing.is_set():
            try:
                entry = self._queues[kind].get(timeout=0.2)
            except queue.Empty:
                continue
            job_id, language, timestamps, temporary = entry
            event = self._events[job_id]
            path = Path(self.get(job_id)["input_path"])
            last_write = [0.0]

            def progress(value, stage):
                if time.monotonic() - last_write[0] >= 0.4:
                    self._update(job_id, progress=round(value, 4), stage=stage)
                    last_write[0] = time.monotonic()
            try:
                if event.is_set():
                    raise TranscriptionCancelled()
                self._update(job_id, status="running", stage="准备模型")
                self.engine_process.ensure_started()
                if event.is_set():
                    raise TranscriptionCancelled()
                result = ASRClient().transcribe(path, language=language, timestamps=timestamps,
                                               progress=progress, cancel=event)
                if event.is_set():
                    raise TranscriptionCancelled()
                item = self.get(job_id)
                stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(item["name"]).stem).strip(" .")[:64] or "录音"
                folder = OUTPUTS / time.strftime("%Y-%m-%d") / f"{stem}-{job_id[:8]}"
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "文字.txt").write_text(result.text, encoding="utf-8")
                (folder / "结果.json").write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
                srt = make_subtitles(result) if timestamps else ""
                if srt:
                    (folder / "字幕.srt").write_text(srt, encoding="utf-8")
                self._update(job_id, status="succeeded", stage="识别完成", progress=1,
                             output_dir=str(folder), result=result.to_dict())
            except TranscriptionCancelled:
                self._update(job_id, status="cancelled", stage="已取消")
            except Exception as exc:
                self._update(job_id, status="failed", stage="识别失败", error=str(exc))
            finally:
                if temporary and path.is_relative_to((DATA / "temp").resolve()):
                    path.unlink(missing_ok=True)
                with self._lock:
                    self._events.pop(job_id, None)
                self._queues[kind].task_done()

    def close(self):
        with self._lock:
            for event in self._events.values():
                event.set()
        self._closing.set()
        for worker in self._workers:
            worker.join(timeout=3)
