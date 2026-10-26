"""Compact Windows floating window. Recognition and HTTP live outside this module."""
import json
import logging
import math
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from .config import DATA, OUTPUTS, ROOT
from .dictation import CapsLockHotkey, Microphone
from .jobs import TERMINAL
from .processes import stop_original_microphone_client
from . import win_input

BG = "#F5F7F5"
CARD = "#FFFFFF"
INK = "#18332D"
MUTED = "#76867F"
GREEN = "#16745C"
PALE = "#E6F1EB"
RED = "#B84545"


class ActionButton(tk.Canvas):
    def __init__(self, master, text, command, *, scale=1, primary=False, height=44, **kwargs):
        self.scale, self.command = scale, command
        self.label, self.primary, self.enabled = text, primary, True
        self.hover = False
        super().__init__(master, height=round(height*scale), highlightthickness=0,
                         bd=0, bg=master.cget("bg"), cursor="hand2", takefocus=1, **kwargs)
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<ButtonRelease-1>", lambda e: self.invoke())
        self.bind("<Return>", lambda e: self.invoke())
        self.bind("<space>", lambda e: self.invoke())
        self.bind("<FocusIn>", lambda e: self.draw())
        self.bind("<FocusOut>", lambda e: self.draw())

    def _hover(self, value):
        self.hover = value
        self.draw()

    def set(self, text=None, enabled=None, primary=None):
        if text is not None:
            self.label = text
        if enabled is not None:
            self.enabled = enabled
        if primary is not None:
            self.primary = primary
        self.configure(cursor="hand2" if self.enabled else "arrow")
        self.draw()

    def invoke(self):
        if self.enabled:
            self.command()

    def draw(self):
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        radius = min(round(12*self.scale), height // 2)
        color = ("#115E4B" if self.hover else GREEN) if self.primary else ("#DFEAE3" if self.hover else PALE)
        foreground = "#FFFFFF" if self.primary else GREEN
        if not self.enabled:
            color, foreground = "#E8ECE9", "#99A69F"
        points = [radius, 1, width-radius, 1, width-1, 1, width-1, radius,
                  width-1, height-radius, width-1, height-1, width-radius, height-1,
                  radius, height-1, 1, height-1, 1, height-radius, 1, radius, 1, 1]
        self.create_polygon(points, smooth=True, fill=color, outline=GREEN if self.focus_get() == self else color)
        self.create_text(width/2, height/2, text=self.label, fill=foreground,
                         font=("Microsoft YaHei UI", -round(13*self.scale), "bold"))


class SpeechWindow:
    def __init__(self, service, api, *, on_close=None):
        self.service, self.api, self.on_close = service, api, on_close
        self.root = tk.Tk()
        self.root.title("Speech Desk - 语音助手")
        self.root.configure(bg=BG)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        self.scale = max(1, self.root.winfo_fpixels("1i") / 96)
        self.p = lambda value: round(value * self.scale)
        self.font = lambda size=12, bold=False: ("Microsoft YaHei UI", -self.p(size), "bold" if bold else "normal")
        self.events = queue.Queue()
        self.mode, self.collapsed, self.pinned = "mic", False, True
        self.record_state, self.record_origin = "idle", None
        self.microphone, self.mic_job = None, None
        self.stop_after_start = threading.Event()
        self.last_window, self.target_window = None, None
        self.file_jobs, self.completed_files = [], set()
        self.result_text, self.output_dir = "", None
        self.closing = False
        self.shortcut_enabled = tk.BooleanVar(value=True)
        self.hotkey = None
        self.service.listeners.append(lambda event, value: self.events.put((event, value)))
        self._build()
        self._place_window()
        self.root.update_idletasks()
        win_input.round_window(self.root)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Escape>", lambda e: self._collapse())
        self.root.report_callback_exception = self._callback_error
        threading.Thread(target=self._prepare_hotkey, daemon=True).start()
        self.service.warm()
        self.root.after(100, self._tick)

    def _label(self, master, text, size=12, color=INK, bold=False, **kwargs):
        return tk.Label(master, text=text, font=self.font(size, bold), bg=master.cget("bg"),
                        fg=color, bd=0, **kwargs)

    def _build(self):
        p = self.p
        shell = tk.Frame(self.root, bg=BG, highlightbackground="#DCE5DE", highlightthickness=1)
        shell.pack(fill="both", expand=True)
        self.header = tk.Frame(shell, bg=BG, height=p(51))
        self.header.pack(fill="x", padx=p(15))
        self.header.pack_propagate(False)
        icon = tk.Canvas(self.header, width=p(29), height=p(29), bg=BG, highlightthickness=0, cursor="hand2")
        icon.pack(side="left", padx=(0, p(8)))
        icon.create_oval(p(1), p(1), p(28), p(28), fill=GREEN, outline="")
        for x, length in [(10, 5), (14, 12), (18, 8)]:
            icon.create_line(p(x), p(14-length/2), p(x), p(14+length/2), fill="white", width=p(2), capstyle="round")
        icon.bind("<Button-1>", lambda e: self._record_action())
        self.title = self._label(self.header, "语音助手", 15, bold=True)
        self.title.pack(side="left")
        for widget in (self.header, self.title):
            widget.bind("<Button-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<Double-Button-1>", lambda e: self._collapse())
        self.close_button = self._small(self.header, "×", self.close, size=20)
        self.close_button.pack(side="right", padx=(p(5), 0))
        self.collapse_button = self._small(self.header, "收起", self._collapse)
        self.collapse_button.pack(side="right", padx=p(4))
        self.pin_button = self._small(self.header, "已置顶", self._pin)
        self.pin_button.pack(side="right", padx=p(4))

        self.body = tk.Frame(shell, bg=BG)
        self.body.pack(fill="both", expand=True, padx=p(18))
        status_row = tk.Frame(self.body, bg=BG)
        status_row.pack(fill="x", pady=(0, p(14)))
        self.status = self._label(status_row, "●  正在载入模型", 11, MUTED)
        self.status.pack(side="left")
        self.power_button = self._small(status_row, "暂停服务", self._toggle_service)
        self.power_button.pack(side="right")
        modes = tk.Frame(self.body, bg="#E8EEE9", padx=p(3), pady=p(3))
        modes.pack(fill="x", pady=(0, p(14)))
        modes.columnconfigure((0, 1), weight=1)
        self.mode_buttons = {}
        for index, (key, label) in enumerate((("mic", "说话打字"), ("file", "录音转文字"))):
            button = tk.Button(modes, text=label, font=self.font(13, True), bd=0, relief="flat",
                cursor="hand2", pady=p(9), command=lambda selected=key: self._switch_mode(selected))
            button.grid(row=0, column=index, sticky="ew")
            self.mode_buttons[key] = button

        self.mode_area = tk.Frame(self.body, bg=BG, height=p(166))
        self.mode_area.pack(fill="x")
        self.mode_area.pack_propagate(False)
        self.mic_card = tk.Frame(self.mode_area, bg=CARD, padx=p(16), pady=p(14))
        self.file_card = tk.Frame(self.mode_area, bg=CARD, padx=p(16), pady=p(14))
        self.mic_card.place(relwidth=1, relheight=1)
        self._label(self.mic_card, "说完，文字就到光标处", 15, bold=True).pack(anchor="w")
        self.mic_hint = self._label(self.mic_card, "按住 CapsLock 说话，松开自动输入", 11, MUTED)
        self.mic_hint.pack(anchor="w", pady=(p(6), p(10)))
        self.record_button = ActionButton(self.mic_card, "开始说话", self._record_action,
                                          scale=self.scale, primary=True, height=45)
        self.record_button.pack(fill="x")
        row = tk.Frame(self.mic_card, bg=CARD)
        row.pack(fill="x", pady=(p(7), 0))
        check = tk.Checkbutton(row, text="CapsLock 快捷键", variable=self.shortcut_enabled,
            bg=CARD, fg=MUTED, activebackground=CARD, selectcolor=CARD, font=self.font(11),
            bd=0, highlightthickness=0, cursor="hand2")
        check.pack(side="left")
        self.cancel_mic = self._small(row, "取消识别", self._cancel_mic)
        self.cancel_mic.pack(side="right")
        self.meter = tk.Canvas(self.mic_card, height=p(4), highlightthickness=0, bg="#EEF3EF")
        self.meter.pack(fill="x", pady=(p(5), 0))

        self._label(self.file_card, "把录音整理成文字", 15, bold=True).pack(anchor="w")
        self._label(self.file_card, "支持音频和视频，可以一次选择多个", 11, MUTED).pack(anchor="w", pady=(p(6), p(10)))
        self.choose_button = ActionButton(self.file_card, "选择音视频文件", self._choose_files,
                                          scale=self.scale, primary=True, height=45)
        self.choose_button.pack(fill="x")
        row = tk.Frame(self.file_card, bg=CARD)
        row.pack(fill="x", pady=(p(10), 0))
        self.file_status = self._label(row, "选择文件后自动开始", 11, MUTED, anchor="w")
        self.file_status.pack(side="left", fill="x", expand=True)
        self.cancel_file = self._small(row, "取消任务", self._cancel_files)
        self.cancel_file.pack(side="right")
        self.file_progress = ttk.Progressbar(self.file_card, mode="determinate", maximum=100)
        self.file_progress.pack(fill="x", pady=(p(8), 0))

        result_header = tk.Frame(self.body, bg=BG)
        result_header.pack(fill="x", pady=(p(17), p(8)))
        self._label(result_header, "识别结果", 12, bold=True).pack(side="left")
        self.result_count = self._label(result_header, "", 10, MUTED)
        self.result_count.pack(side="right")
        result_box = tk.Frame(self.body, bg=CARD, padx=p(12), pady=p(9))
        result_box.pack(fill="both", expand=True)
        self.preview = tk.Text(result_box, font=self.font(13), bg=CARD, fg=MUTED,
            relief="flat", bd=0, height=4, wrap="word", highlightthickness=0,
            selectbackground="#CBE3D5", selectforeground=INK, cursor="xterm", spacing1=p(3), spacing3=p(3))
        self.preview.pack(fill="both", expand=True)
        self.preview.insert("1.0", "识别后的文字会显示在这里")
        self.preview.configure(state="disabled")
        actions = tk.Frame(self.body, bg=BG)
        actions.pack(fill="x", pady=(p(10), 0))
        actions.columnconfigure((0, 1), weight=1)
        self.copy_button = ActionButton(actions, "复制文字", self._copy, scale=self.scale, height=36)
        self.copy_button.grid(row=0, column=0, sticky="ew", padx=(0, p(5)))
        self.folder_button = ActionButton(actions, "打开结果", self._open_output, scale=self.scale, height=36)
        self.folder_button.grid(row=0, column=1, sticky="ew", padx=(p(5), 0))
        self.note = self._label(self.body, "", 10, MUTED, anchor="w", wraplength=p(335))
        self.note.pack(fill="x", pady=(p(7), 0))
        footer = tk.Frame(self.body, bg=BG, height=p(36))
        footer.pack(fill="x")
        self._label(footer, "Qwen3 ASR · 本地离线", 10, MUTED).pack(side="left")
        self._small(footer, "接口说明", self._open_api_docs, size=10).pack(side="right")
        self._switch_mode("mic")

    def _small(self, parent, text, command, size=11):
        return tk.Button(parent, text=text, command=command, bg=parent.cget("bg"),
            activebackground=parent.cget("bg"), fg=MUTED, activeforeground=GREEN,
            font=self.font(size), bd=0, relief="flat", cursor="hand2", padx=self.p(2), pady=0)

    def _place_window(self):
        width, height = self.p(380), self.p(590)
        screen_width, screen_height = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x, y = screen_width-width-self.p(35), max(self.p(30), (screen_height-height)//2)
        try:
            settings = json.loads((DATA / "ui-settings.json").read_text(encoding="utf-8"))
            x, y = settings.get("x", x), settings.get("y", y)
        except (OSError, ValueError):
            pass
        x, y = max(0, min(x, screen_width-width)), max(0, min(y, screen_height-height))
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _drag_start(self, event):
        self.drag = (event.x_root-self.root.winfo_x(), event.y_root-self.root.winfo_y())

    def _drag_move(self, event):
        self.root.geometry(f"+{event.x_root-self.drag[0]}+{event.y_root-self.drag[1]}")

    def _pin(self):
        self.pinned = not self.pinned
        self.root.attributes("-topmost", self.pinned)
        self.pin_button.configure(text="已置顶" if self.pinned else "置顶")

    def _collapse(self):
        self.collapsed = not self.collapsed
        if self.collapsed:
            self.body.pack_forget()
            self.root.geometry(f"{self.p(290)}x{self.p(53)}")
        else:
            self.body.pack(fill="both", expand=True, padx=self.p(18))
            self.root.geometry(f"{self.p(380)}x{self.p(590)}")
        self.collapse_button.configure(text="展开" if self.collapsed else "收起")

    def _switch_mode(self, mode):
        self.mode = mode
        for key, button in self.mode_buttons.items():
            button.configure(bg=CARD if mode == key else "#E8EEE9", fg=INK if mode == key else MUTED,
                             activebackground=CARD if mode == key else "#E8EEE9")
        self.mic_card.place_forget()
        self.file_card.place_forget()
        (self.mic_card if mode == "mic" else self.file_card).place(relwidth=1, relheight=1)

    def _callback_error(self, exception, value, traceback):
        logging.error("GUI callback failed", exc_info=(exception, value, traceback))
        self._note(str(value), True)

    def _note(self, text, error=False):
        self.note.configure(text=text, fg=RED if error else MUTED)

    def _prepare_hotkey(self):
        try:
            stop_original_microphone_client()
            hotkey = CapsLockHotkey(lambda event: self.events.put((event, None)))
            self.events.put(("hotkey_ready", hotkey))
        except Exception as exc:
            self.events.put(("hotkey_error", str(exc)))

    def _set_result(self, text, folder=None):
        self.result_text, self.output_dir = text, folder
        self.preview.configure(state="normal", fg=INK)
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text if text else "没有识别到文字，可以再试一次")
        self.preview.configure(state="disabled")
        self.result_count.configure(text=f"{len(text)} 字" if text else "")

    def _copy(self):
        if self.result_text:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.result_text)
            self._note("已复制，可以粘贴到其他应用")

    def _open_output(self):
        path = Path(self.output_dir) if self.output_dir else OUTPUTS
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def _open_api_docs(self):
        os.startfile(ROOT / "desktop" / "接口接入说明.md")

    def _toggle_service(self):
        if self.record_state != "idle":
            return self._note("请先结束说话或等待本次识别完成", True)
        if not self.service.enabled or self.service.engine.error:
            self._note("")
            self.service.warm()
            return
        self.power_button.configure(state="disabled")
        def run():
            try:
                self.service.stop_engine()
                self.events.put(("service_stopped", None))
            except Exception as exc:
                self.events.put(("service_error", str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def _record_action(self):
        if self.record_state in {"starting", "recording"}:
            self._stop_record()
        elif self.record_state == "idle":
            self._start_record("button")

    def _start_record(self, origin):
        if self.record_state != "idle":
            return
        if not self.service.enabled or not self.service.engine.ready:
            return self._note("请等待模型就绪后再说话", True)
        current = win_input.external_window()
        self.target_window = current or self.last_window
        self.record_origin, self.record_state = origin, "starting"
        self.stop_after_start.clear()
        self.microphone = Microphone()
        self._note("识别后会输入到刚才的窗口" if self.target_window else "请先点一下要输入的窗口；文字也可手动复制")
        def run():
            try:
                name = self.microphone.start()
                self.events.put(("mic_started", name))
            except Exception as exc:
                logging.exception("Cannot start microphone")
                self.events.put(("mic_error", "无法打开麦克风，请检查系统麦克风设置。" + str(exc)))
        threading.Thread(target=run, daemon=True).start()
        if origin == "button" and self.target_window:
            self.root.after(80, lambda: win_input.restore(self.target_window[0]))

    def _stop_record(self):
        if self.record_state == "starting":
            self.stop_after_start.set()
            return
        if self.record_state != "recording":
            return
        self.record_state = "stopping"
        recorder = self.microphone
        def run():
            try:
                path = recorder.stop()
                if recorder.error:
                    raise RuntimeError("录音中断，请重新录制。" + recorder.error)
                if time.monotonic()-recorder.started < 0.3:
                    path.unlink(missing_ok=True)
                    raise RuntimeError("说话时间太短，请再试一次")
                item = self.service.submit(path, kind="mic", timestamps=False,
                                           name="说话输入.wav", temporary=True)
                self.events.put(("mic_submitted", item["id"]))
            except Exception as exc:
                self.events.put(("mic_error", str(exc)))
        threading.Thread(target=run, daemon=True).start()
        if self.target_window:
            win_input.restore(self.target_window[0])

    def _cancel_mic(self):
        if self.mic_job:
            self.service.jobs.cancel(self.mic_job)

    def _choose_files(self):
        if not self.service.enabled:
            return self._note("请先开启识别服务", True)
        paths = filedialog.askopenfilenames(parent=self.root, title="选择录音或视频（可多选）",
            filetypes=[("音频和视频", "*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.wma *.mp4 *.mkv *.mov *.webm"),
                       ("所有文件", "*.*")])
        if paths:
            self.submit_files(paths)

    def submit_files(self, paths):
        self._switch_mode("file")
        for path in paths:
            try:
                self.file_jobs.append(self.service.submit(path)["id"])
            except Exception as exc:
                self._note(str(exc), True)
        self._note("完成后点击“打开结果”查看文字和字幕")

    def _cancel_files(self):
        for job_id in self.file_jobs:
            if self.service.jobs.get(job_id)["status"] not in TERMINAL:
                self.service.jobs.cancel(job_id)

    def _handle_event(self, event, data):
        if event == "hotkey_ready":
            self.hotkey = data
        elif event == "hotkey_error":
            self.shortcut_enabled.set(False)
            self._note("快捷键不可用，仍可点击按钮说话。" + data, True)
        elif event == "start_hotkey":
            self._start_record("hotkey")
        elif event == "stop_hotkey":
            if self.record_origin == "hotkey":
                self._stop_record()
        elif event == "mic_started":
            self.record_state = "recording"
            if self.stop_after_start.is_set():
                self._stop_record()
        elif event == "mic_submitted":
            self.mic_job, self.record_state = data, "processing"
        elif event == "mic_error":
            self.record_state, self.record_origin = "idle", None
            self._note(data, True)
        elif event == "typed":
            self.record_state, self.record_origin = "idle", None
            self._note(data or "已输入到刚才的窗口", bool(data))
        elif event == "service_stopped":
            self.power_button.configure(state="normal")
            self._note("识别已暂停，点击“开启服务”可恢复")
        elif event == "service_error":
            self.power_button.configure(state="normal")
            self._note(data, True)
        elif event == "job_submitted" and data["kind"] == "file":
            if data["id"] not in self.file_jobs:
                self.file_jobs.append(data["id"])
                self._switch_mode("file")
        elif event == "shutdown":
            self.close()

    def _poll_jobs(self):
        if self.mic_job:
            job = self.service.jobs.get(self.mic_job)
            if job["status"] in TERMINAL:
                self.mic_job = None
                if job["status"] == "succeeded":
                    self.record_state = "inserting"
                    text = job["result"]["text"]
                    self._set_result(text, job["output_dir"])
                    target = self.target_window[0] if self.target_window else None
                    def output():
                        error = ""
                        try:
                            if target:
                                win_input.type_text(text, target)
                            else:
                                error = "文字已识别，点击“复制文字”后粘贴即可"
                        except Exception as exc:
                            error = str(exc)
                        self.events.put(("typed", error))
                    threading.Thread(target=output, daemon=True).start()
                else:
                    self.record_state, self.record_origin = "idle", None
                    self._note(job["error"] if job["status"] == "failed" else "已取消识别", job["status"] == "failed")
        pending = []
        for job_id in self.file_jobs:
            job = self.service.jobs.get(job_id)
            if job["status"] in TERMINAL:
                if job_id not in self.completed_files:
                    self.completed_files.add(job_id)
                    if job["status"] == "succeeded":
                        self._set_result(job["result"]["text"], job["output_dir"])
                        self._note("转文字完成，已保存文字和字幕")
                    elif job["status"] == "failed":
                        self._note(job["error"], True)
            else:
                pending.append(job)
        if pending:
            job = pending[0]
            name = job["name"]
            if len(name) > 17:
                name = name[:14] + "…"
            self.file_status.configure(text=f"{len(pending)} 个待完成 · {name}")
            self.file_progress["value"] = job["progress"] * 100
        elif self.file_jobs:
            success = sum(self.service.jobs.get(j)["status"] == "succeeded" for j in self.file_jobs)
            self.file_status.configure(text=f"本次已完成 {success} / {len(self.file_jobs)} 个")
            self.file_progress["value"] = 100 if success else 0
        self.cancel_file.configure(state="normal" if pending else "disabled")

    def _tick(self):
        if self.closing:
            return
        try:
            current = win_input.external_window()
            if current:
                self.last_window = current
            while True:
                try:
                    event, data = self.events.get_nowait()
                except queue.Empty:
                    break
                self._handle_event(event, data)
                if self.closing:
                    return
            engine = self.service.engine
            ready = engine.ready and self.service.enabled
            if engine.loading:
                self.status.configure(text="●  正在载入模型", fg=MUTED)
            elif not self.service.enabled:
                self.status.configure(text="●  识别已暂停", fg=MUTED)
            elif engine.error:
                self.status.configure(text="●  模型启动失败", fg=RED)
                self._note(engine.error, True)
            elif self.record_state in {"starting", "recording", "stopping"}:
                self.status.configure(text="●  正在听你说话", fg=RED)
            else:
                self.status.configure(text="●  已就绪 · 离线识别" if ready else "●  正在准备", fg=GREEN if ready else MUTED)
            self.power_button.configure(text="重试" if engine.error else "暂停服务" if self.service.enabled else "开启服务")
            if self.hotkey:
                enabled = bool(self.shortcut_enabled.get() and ready)
                if enabled != self.hotkey.enabled:
                    self.hotkey.set_enabled(enabled)
            if self.record_state == "recording":
                seconds = max(0, int(time.monotonic()-self.microphone.started))
                self.record_button.set(f"结束并输入   {seconds//60:02}:{seconds%60:02}", True)
                self.mic_hint.configure(text="正在录音 · 说完后点击结束", fg=RED)
                level = self.microphone.level
                self.meter.delete("all")
                self.meter.create_rectangle(0, 0, self.meter.winfo_width()*max(0.03, level), self.p(4), fill=GREEN, outline="")
                if seconds >= 600:
                    self._stop_record()
            elif self.record_state in {"starting", "stopping", "processing", "inserting"}:
                label = {"starting": "正在打开麦克风…", "stopping": "正在结束录音…",
                         "processing": "正在识别…", "inserting": "正在输入…"}[self.record_state]
                self.record_button.set(label, self.record_state == "starting")
                self.mic_hint.configure(text="稍等片刻，文字识别后会自动输入", fg=MUTED)
            else:
                self.record_button.set("开始说话", ready)
                self.mic_hint.configure(text="按住 CapsLock 说话，松开自动输入", fg=MUTED)
                self.meter.delete("all")
            self.cancel_mic.configure(state="normal" if self.mic_job else "disabled")
            self.choose_button.set(enabled=ready)
            self.copy_button.set(enabled=bool(self.result_text))
            self.folder_button.set(enabled=bool(self.output_dir))
            self._poll_jobs()
        except Exception:
            logging.exception("Window update failed")
        if not self.closing:
            self.root.after(120, self._tick)

    def close(self):
        active = self.record_state != "idle" or any(self.service.jobs.get(j)["status"] not in TERMINAL for j in self.file_jobs)
        if active and not messagebox.askyesno("退出语音助手", "当前识别还未完成，退出将取消任务。确定退出吗？", parent=self.root):
            return
        self.closing = True
        if self.hotkey:
            self.hotkey.close()
        try:
            settings = {"x": self.root.winfo_x(), "y": self.root.winfo_y()}
            (DATA / "ui-settings.json").write_text(json.dumps(settings), encoding="utf-8")
            if self.microphone:
                self.microphone.stop()
        except Exception:
            logging.exception("Window cleanup failed")
        self.root.withdraw()
        if self.on_close:
            self.on_close()
        self.root.destroy()

    def run(self):
        self.root.mainloop()
