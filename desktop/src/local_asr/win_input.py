"""Small Windows adapter. No GUI or ASR dependency."""
import ctypes
from ctypes import wintypes
import os
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND


def foreground():
    window = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
    title = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(window, title, 512)
    return int(window or 0), pid.value, title.value


def external_window():
    window, pid, title = foreground()
    if window and pid != os.getpid() and title and title not in {"Program Manager", "任务切换"}:
        return window, title
    return None


def restore(window):
    if not window or not user32.IsWindow(window):
        return False
    if user32.IsIconic(window):
        user32.ShowWindow(window, 9)
    user32.SetForegroundWindow(window)
    time.sleep(0.1)
    return int(user32.GetForegroundWindow() or 0) == int(window)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", wintypes.DWORD), ("data", INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT


def type_text(text, window):
    if not text:
        return
    if not restore(window):
        raise RuntimeError("没有找到输入窗口，文字已保留，请点击“复制文字”")
    encoded = text.encode("utf-16-le")
    units = [int.from_bytes(encoded[i:i+2], "little") for i in range(0, len(encoded), 2)]
    for offset in range(0, len(units), 32):
        if int(user32.GetForegroundWindow() or 0) != int(window):
            raise RuntimeError("输入窗口已切换，剩余文字请从识别结果中复制")
        batch = []
        for unit in units[offset:offset+32]:
            for flags in (0x0004, 0x0004 | 0x0002):
                event = INPUT(type=1)
                event.ki = KEYBDINPUT(0, unit, flags, 0, 0)
                batch.append(event)
        array = (INPUT * len(batch))(*batch)
        if user32.SendInput(len(batch), array, ctypes.sizeof(INPUT)) != len(batch):
            raise RuntimeError("目标窗口无法自动输入，请点击“复制文字”后粘贴")


def round_window(root):
    try:
        hwnd = user32.GetAncestor(root.winfo_id(), 2)
        preference = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), 33,
                                                  ctypes.byref(preference), 4)
    except Exception:
        pass
