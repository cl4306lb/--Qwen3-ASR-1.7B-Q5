from pathlib import Path
import os
import sys


def workspace_root():
    if os.environ.get("SPEECH_DESK_ROOT"):
        return Path(os.environ["SPEECH_DESK_ROOT"]).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parents[3]


ROOT = workspace_root()
VENDOR = ROOT / "CapsWriter-Offline"
DATA = ROOT / "desktop" / "data"
OUTPUTS = ROOT / "识别结果"
WS_URL = "ws://127.0.0.1:6016"
HTTP_PORT = 8765


def prepare_folders():
    for folder in (DATA, DATA / "logs", DATA / "temp", OUTPUTS):
        folder.mkdir(parents=True, exist_ok=True)
