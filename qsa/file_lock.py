"""Small cross-process data lock; SQLite transactions protect V2 metadata."""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_locks, _guard, _local = {}, threading.Lock(), threading.local()


@contextmanager
def data_lock(root, timeout=30):
    path = Path(root).resolve() / ".writer.lock"
    name = str(path)
    with _guard:
        lock = _locks.setdefault(name, threading.RLock())
    with lock:
        held = getattr(_local, "held", set())
        if name in held:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0"); stream.flush()
            started = time.monotonic()
            while True:
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() - started >= timeout:
                        raise TimeoutError("数据正在被其他进程更新，请稍后重试")
                    time.sleep(.05)
            _local.held = held | {name}
            try:
                yield
            finally:
                _local.held = held
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
