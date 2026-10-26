import logging
import threading
from .jobs import JobManager
from .processes import EngineProcess


class SpeechService:
    """Engine lifecycle and jobs, shared by desktop and headless entry points."""
    def __init__(self):
        self.engine = EngineProcess()
        self.jobs = JobManager(self.engine)
        self.enabled = True
        self._warm_thread = None
        self.listeners = []
        self.shutdown_callback = None
        self._closing = threading.Event()
        threading.Thread(target=self._monitor, daemon=True, name="EngineMonitor").start()

    def _monitor(self):
        while not self._closing.wait(5):
            if self.enabled and self.engine.ready and not self.engine.probe():
                self.engine.ready = False
                self.engine.error = "识别服务已断开，请点击重试"

    def warm(self):
        if self._warm_thread and self._warm_thread.is_alive():
            return
        def run():
            try:
                self.engine.ensure_started()
            except Exception:
                logging.exception("Recognition engine could not start")
        self.enabled = True
        self._warm_thread = threading.Thread(target=run, daemon=True, name="ModelWarmup")
        self._warm_thread.start()

    def health(self):
        return dict(service="speech-desk", version="1.0.0", model="Qwen3-ASR-1.7B-Q5_K",
                    enabled=self.enabled, ready=self.engine.ready, loading=self.engine.loading,
                    error=self.engine.error)

    def submit(self, path, **kwargs):
        if not self.enabled:
            raise RuntimeError("识别服务已关闭，请先打开服务")
        job = self.jobs.submit(path, **kwargs)
        for listener in list(self.listeners):
            try:
                listener("job_submitted", job)
            except Exception:
                logging.exception("Service listener failed")
        return job

    def stop_engine(self):
        if any(j["status"] not in {"succeeded", "failed", "cancelled"} for j in self.jobs.list_jobs(1000)):
            raise RuntimeError("请等待当前任务完成或取消后，再关闭服务")
        if self.engine.loading:
            raise RuntimeError("模型正在载入，请稍后关闭")
        self.engine.stop(include_existing=True)
        self.enabled = False

    def close(self, stop_shared=False):
        self._closing.set()
        self.jobs.close()
        self.engine.stop(include_existing=stop_shared)
