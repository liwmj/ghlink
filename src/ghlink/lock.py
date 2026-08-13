"""跨平台文件锁（防重入）：定时跑 + 手动跑不重叠。

实现要点：
- 锁文件内容 = PID + 时间戳；acquire 时检查：
  a) 锁文件不存在 → 获取
  b) 存在且 PID 存活 → 已持有，跳过本轮（不阻塞不排队）
  c) 存在但 PID 已死/超时（如 10min 过期）→ 视为残留锁，接管
- Windows 用 PID 文件（进程内互斥足够）；Linux/macOS 用 fcntl.flock（进程退出自动释放）
"""
import os
import sys
import time
from contextlib import contextmanager
from typing import Iterator


def _pid_alive(pid: int) -> bool:
    """判断 PID 是否存活（跨平台）。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@contextmanager
def acquire(lock_path: str, stale_after_sec: int = 600) -> Iterator[bool]:
    """获取锁；成功 yield True，已被持有 yield False（调用方直接退出本轮）。"""
    # Linux/macOS 用 flock 内核锁（进程退出自动释放）
    if sys.platform != "win32":
        try:
            import fcntl
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.ftruncate(fd, 0)
                os.write(fd, f"{os.getpid()} {int(time.time())}".encode())
                yield True
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
            return
        except (ImportError, OSError):
            pass  # 回退 PID 文件方案

    # Windows：用 msvcrt.locking 内核锁（避免 PID 文件检查-创建竞态）
    if sys.platform == "win32":
        try:
            import msvcrt
        except ImportError:
            pass  # 平台不支持 → 回退 PID 文件方案
        else:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            except OSError:
                yield False  # 锁文件不可打开，视为被占用，跳过本轮
                return
            try:
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                except OSError:
                    yield False  # 锁被持有 → 跳过本轮（不排队不阻塞）
                    return
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, f"{os.getpid()} {int(time.time())}".encode())
                yield True
            finally:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
                os.close(fd)
            return

    # 无 flock/msvcrt：PID 文件 + 残留接管（跨平台兜底）
    acquired = False
    try:
        if os.path.exists(lock_path):
            try:
                with open(lock_path, "r", encoding="utf-8") as f:
                    pid_str, ts_str = f.read().split()
                pid, ts = int(pid_str), float(ts_str)
                if _pid_alive(pid) and (time.time() - ts) < stale_after_sec:
                    yield False
                    return
            except (OSError, ValueError):
                pass  # 锁文件损坏 → 视为残留，接管
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write(f"{os.getpid()} {int(time.time())}")
        acquired = True
        yield True
    finally:
        if acquired and os.path.exists(lock_path):
            try:
                with open(lock_path, "r", encoding="utf-8") as f:
                    pid_str = f.read().split()[0]
                if int(pid_str) == os.getpid():
                    os.unlink(lock_path)
            except (OSError, ValueError, IndexError):
                pass
