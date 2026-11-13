"""Export a map to PNG by driving Wonderdraft itself (Windows only, ctypes, no extra deps).

Sequence (docs/format.md "Spike C"): launch with the map, wait for its window, send Ctrl+E and
Enter with SendInput, then fill the native "Export" save dialog with WM_SETTEXT / BM_CLICK and
wait for the file to appear and stop growing. Wonderdraft is always closed afterwards.
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable

DEFAULT_EXE = r"C:\Program Files\Wonderdraft\Wonderdraft.exe"
DIALOG_TITLE = "Export"
EDIT_ID = 1001
SAVE_BUTTON_ID = 1
POLL_INTERVAL = 0.7
log = logging.getLogger("wdmcp")


class ExportError(RuntimeError):
    """The automated export failed; the message says why."""


def _exe() -> Path:
    return Path(os.environ.get("WDMCP_EXE") or DEFAULT_EXE)


def is_supported() -> bool:
    return sys.platform == "win32" and _exe().is_file()


# ------------------------------------------------------------- pure helpers
def title_matches(title: str, stem: str) -> bool:
    """Wonderdraft's window title starts with the map's file stem once the map is loaded."""
    return bool(title) and title.lower().startswith(stem.lower())


def wait_for_stable_size(get_size: Callable[[], int | None], *, deadline: float,
                         clock: Callable[[], float] = time.monotonic,
                         sleep: Callable[[float], None] = time.sleep,
                         interval: float = POLL_INTERVAL) -> int | None:
    """Poll ``get_size`` (None = file absent) until a non-empty size repeats on two
    consecutive polls. Returns that size, or None if ``deadline`` (clock time) passes."""
    last = None
    while clock() < deadline:
        size = get_size()
        if size is not None and size > 0 and size == last:
            return size
        last = size
        sleep(interval)
    return None


# ------------------------------------------------------------------ Win32
def _user32():
    u = ctypes.WinDLL("user32", use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    u.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    u.EnumChildWindows.argtypes = [wintypes.HWND, enum_proc, wintypes.LPARAM]
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetDlgCtrlID.argtypes = [wintypes.HWND]
    u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u.SendMessageW.restype = ctypes.c_ssize_t
    u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.GetForegroundWindow.restype = wintypes.HWND
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    u.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
    u.MapVirtualKeyW.restype = wintypes.UINT
    u.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
    return u, enum_proc


def _text(fn, hwnd) -> str:
    buf = ctypes.create_unicode_buffer(512)
    fn(hwnd, buf, 512)
    return buf.value


def _find_windows(pid: int, *, visible_only: bool = True) -> list[tuple[int, str, str]]:
    """Top-level windows of ``pid`` as (hwnd, class name, title)."""
    u, enum_proc = _user32()
    found: list[tuple[int, str, str]] = []

    def cb(hwnd, _):
        owner = wintypes.DWORD(0)
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and (u.IsWindowVisible(hwnd) or not visible_only):
            found.append((hwnd, _text(u.GetClassNameW, hwnd), _text(u.GetWindowTextW, hwnd)))
        return True

    u.EnumWindows(enum_proc(cb), 0)
    return found


def _wonderdraft_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Wonderdraft.exe", "/NH"],
                             capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "wonderdraft.exe" in out.lower()


def _foreground(hwnd) -> bool:
    """Bring ``hwnd`` to the foreground, falling back to the ALT-key and AttachThreadInput tricks."""
    u, _ = _user32()
    u.ShowWindow(hwnd, 9)  # SW_RESTORE
    if u.SetForegroundWindow(hwnd) and u.GetForegroundWindow() == hwnd:
        return True
    vk_menu, keyup = 0x12, 0x2
    u.keybd_event(vk_menu, 0, 0, 0)  # an ALT press lifts the foreground lock
    u.keybd_event(vk_menu, 0, keyup, 0)
    u.SetForegroundWindow(hwnd)
    if u.GetForegroundWindow() == hwnd:
        return True
    k32 = ctypes.WinDLL("kernel32")
    fg = u.GetForegroundWindow()
    this = k32.GetCurrentThreadId()
    fg_thread = u.GetWindowThreadProcessId(fg, None) if fg else 0
    if fg_thread and fg_thread != this:
        u.AttachThreadInput(this, fg_thread, True)
        try:
            u.SetForegroundWindow(hwnd)
        finally:
            u.AttachThreadInput(this, fg_thread, False)
    time.sleep(0.1)
    return u.GetForegroundWindow() == hwnd


ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):  # only here so the union has the right size
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class _InputUnion(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _InputUnion)]


def _send_event(u, vk: int, up: bool) -> None:
    i = INPUT(type=1)  # INPUT_KEYBOARD
    i.ki = KEYBDINPUT(vk, u.MapVirtualKeyW(vk, 0), 0x2 if up else 0, 0, 0)
    if u.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT)) != 1:
        raise ExportError("SendInput was blocked (is Wonderdraft running elevated?)")


def _send_keys(chords: list[list[int]]) -> None:
    """Send each chord (virtual keys, modifiers first) one event per SendInput call.

    Godot polls input once per frame, so a press and release in the same frame is lost:
    events are spaced 40 ms apart and the chord is held 80 ms before the releases."""
    u, _ = _user32()
    u.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    u.SendInput.restype = wintypes.UINT
    for chord in chords:
        for vk in chord:
            _send_event(u, vk, False)
            time.sleep(0.04)
        time.sleep(0.08)
        for vk in reversed(chord):
            _send_event(u, vk, True)
            time.sleep(0.04)
        log.info("sent keys %s", [hex(v) for v in chord])


def _fill_save_dialog(pid: int, out_path: str, timeout: float = 15.0) -> None:
    """Wait for the native "Export" dialog of ``pid``, set the file name and press Save."""
    u, enum_proc = _user32()
    wm_settext, bm_click = 0x000C, 0x00F5
    dlg = _wait_dialog(pid, timeout)
    if dlg is None:
        raise ExportError(f"the Export dialog did not appear within {timeout:.0f} s")

    edit = save = None

    def cb(hwnd, _):
        nonlocal edit, save
        cls, cid = _text(u.GetClassNameW, hwnd), u.GetDlgCtrlID(hwnd)
        if cls == "Edit" and cid == EDIT_ID and edit is None:
            edit = hwnd
        if cls == "Button" and cid == SAVE_BUTTON_ID and save is None:
            save = hwnd
        return True

    u.EnumChildWindows(dlg, enum_proc(cb), 0)
    if edit is None or save is None:
        raise ExportError("the Export dialog has no file name box or Save button (layout changed?)")
    buf = ctypes.create_unicode_buffer(out_path)
    u.SendMessageW(edit, wm_settext, 0, ctypes.addressof(buf))
    time.sleep(0.2)
    u.SendMessageW(save, bm_click, 0, 0)


def _find_dialog(pid: int) -> int | None:
    for hwnd, cls, title in _find_windows(pid):
        if cls == "#32770" and title == DIALOG_TITLE:
            return hwnd
    return None


def _wait_dialog(pid: int, timeout: float) -> int | None:
    end = time.monotonic() + timeout
    while True:
        dlg = _find_dialog(pid)
        if dlg is not None or time.monotonic() >= end:
            return dlg
        time.sleep(0.25)


def _open_export_dialog(pid: int, main: int) -> None:
    """Ctrl+E then Enter; if the native dialog does not show within 5 s, retry once."""
    for attempt in (1, 2):
        fg = _foreground(main)
        log.info("export attempt %d: foreground=%s", attempt, fg)
        if not fg:
            if attempt == 2:
                raise ExportError("could not bring Wonderdraft to the foreground")
            continue
        time.sleep(0.3)
        _send_keys([[0x11, 0x45]])  # Ctrl+E
        time.sleep(1.5)
        _send_keys([[0x0D]])  # Enter: accept the Export Options defaults
        if _wait_dialog(pid, 5.0) is not None:
            log.info("export dialog appeared (attempt %d)", attempt)
            return
        log.info("export dialog not seen after attempt %d", attempt)


def _close(proc: subprocess.Popen, grace: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    try:
        u, _ = _user32()
        for hwnd, _cls, _t in _find_windows(proc.pid, visible_only=False):
            u.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        proc.wait(timeout=grace)
    except Exception:
        pass
    if proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.SubprocessError:
            pass


# ------------------------------------------------------------------- entry
def export_via_app(map_path, out_path, timeout: float = 120) -> Path:
    """Export ``map_path`` (a saved .wonderdraft_map) to a PNG at ``out_path`` via Wonderdraft."""
    if sys.platform != "win32":
        raise ExportError("exporting through Wonderdraft is only supported on Windows")
    exe = _exe()
    if not exe.is_file():
        raise ExportError(f"Wonderdraft not found at {exe}; set WDMCP_EXE")
    map_p = Path(map_path).resolve()
    out_p = Path(out_path).resolve()
    if not map_p.is_file():
        raise ExportError(f"map file not found: {map_p}")
    if out_p.exists():
        raise ExportError(f"output already exists: {out_p}")
    if _wonderdraft_running():
        raise ExportError("Wonderdraft is already running. Close it first: the automation "
                          "needs a fresh instance.")
    deadline = time.monotonic() + timeout
    proc = subprocess.Popen([str(exe), str(map_p)])
    try:
        stem = map_p.stem
        main = None
        while main is None:
            if proc.poll() is not None:
                raise ExportError("Wonderdraft exited before the map window appeared")
            main = next((h for h, _c, t in _find_windows(proc.pid) if title_matches(t, stem)), None)
            if main is None:
                if time.monotonic() > deadline:
                    raise ExportError(f"timed out after {timeout:.0f} s waiting for the map window")
                time.sleep(0.5)
        time.sleep(3)  # let the canvas finish its first render
        _open_export_dialog(proc.pid, main)
        _fill_save_dialog(proc.pid, str(out_p),
                          timeout=min(15.0, max(1.0, deadline - time.monotonic())))
        size = wait_for_stable_size(
            lambda: out_p.stat().st_size if out_p.exists() else None, deadline=deadline)
        if size is None:
            raise ExportError(f"timed out after {timeout:.0f} s; the export file did not "
                              "appear or kept changing")
        return out_p
    finally:
        _close(proc)
