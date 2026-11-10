"""Windows-only local notifications; no remote messaging or credentials."""
from __future__ import annotations


def balloon(title, body):
    import ctypes
    import os
    import time
    from ctypes import wintypes
    if os.name != "nt":
        return False
    kernel, user, shell = ctypes.windll.kernel32, ctypes.windll.user32, ctypes.windll.shell32
    session = wintypes.DWORD()
    if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)) or session.value != kernel.WTSGetActiveConsoleSessionId():
        return False
    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte*8)]
    class NotifyData(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR*128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR*256), ("uTimeout", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR*64),
            ("dwInfoFlags", wintypes.DWORD), ("guidItem", GUID), ("hBalloonIcon", wintypes.HICON)]
    user.CreateWindowExW.restype = wintypes.HWND
    user.LoadIconW.restype = wintypes.HICON
    user.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    user.DestroyWindow.argtypes = [wintypes.HWND]
    hwnd = user.CreateWindowExW(0, "STATIC", "Zhangshi notification", 0, 0, 0, 0, 0, None, None, None, None)
    if not hwnd:
        return False
    data = NotifyData()
    data.cbSize = ctypes.sizeof(data)
    data.hWnd, data.uID, data.uFlags = hwnd, 1, 2 | 4 | 16
    data.hIcon = user.LoadIconW(None, ctypes.c_void_p(32516))
    data.szTip, data.szInfoTitle, data.szInfo = "涨势研究台", str(title)[:63], str(body)[:255]
    data.dwInfoFlags, data.uTimeout = 1, 5000
    try:
        ok = bool(shell.Shell_NotifyIconW(0, ctypes.byref(data)))
        if ok:
            time.sleep(3)
        return ok
    finally:
        shell.Shell_NotifyIconW(2, ctypes.byref(data))
        user.DestroyWindow(hwnd)
