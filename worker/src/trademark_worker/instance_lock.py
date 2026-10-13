"""Process-wide ownership guard for the desktop Worker's local browser session."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os


WORKER_MUTEX_NAME = r"Local\TrademarkQueryDesktop-Worker"
ERROR_ALREADY_EXISTS = 183


@dataclass(slots=True)
class WorkerInstanceLock:
    """Own a Windows named mutex until the Worker exits or crashes."""

    handle: int | None
    _kernel32: object | None = None

    @classmethod
    def acquire(cls, name: str = WORKER_MUTEX_NAME) -> "WorkerInstanceLock | None":
        if os.name != "nt":
            return cls(None)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (
            ctypes.c_void_p,
            ctypes.c_bool,
            ctypes.c_wchar_p,
        )
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_bool
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "无法创建 Worker 单实例锁")
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return None
        return cls(int(handle), kernel32)

    def close(self) -> None:
        if self.handle is None or self._kernel32 is None:
            return
        self._kernel32.CloseHandle(ctypes.c_void_p(self.handle))
        self.handle = None

    def __enter__(self) -> "WorkerInstanceLock":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
