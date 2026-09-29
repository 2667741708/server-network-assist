"""Serialize native ownership and crash recovery across processes."""
import ctypes
import hashlib
import os
from pathlib import Path
import time


class CustomerLock:
    def __init__(self, data, timeout=0):
        self.data, self.timeout = Path(data), timeout
        self.handle = None

    def __enter__(self):
        if os.name == 'nt':
            from ctypes import wintypes
            self.kernel = ctypes.windll.kernel32
            self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            self.kernel.CreateMutexW.restype = wintypes.HANDLE
            self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self.kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            name = hashlib.sha256(os.path.normcase(str(self.data.resolve())).encode()).hexdigest()
            self.handle = self.kernel.CreateMutexW(None, False, 'Global\\SNA-Customer-' + name)
            if not self.handle:
                raise RuntimeError('无法建立客户端所有权锁，保持原网络不启动')
            result = self.kernel.WaitForSingleObject(self.handle, int(self.timeout * 1000))
            if result not in (0, 0x80):
                self.kernel.CloseHandle(self.handle); self.handle = None
                raise RuntimeError('客户端或恢复程序仍在运行，请使用已有窗口或稍后重试')
        else:
            import fcntl
            self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.handle = (self.data / 'client-owner.lock').open('a')
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        self.handle.close(); self.handle = None
                        raise RuntimeError('客户端或恢复程序仍在运行，请稍后重试') from None
                    time.sleep(.05)
        return self

    def __exit__(self, *exc):
        if self.handle is not None:
            if os.name == 'nt':
                self.kernel.ReleaseMutex(self.handle)
                self.kernel.CloseHandle(self.handle)
            else:
                self.handle.close()
            self.handle = None
