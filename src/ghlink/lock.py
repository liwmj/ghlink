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


def _safe_lock_path(lock_path: str) -> str:
    """校验并规范化锁文件路径（SonarCloud S8707：防符号链接/路径逃逸）。

    要求：绝对路径 + realpath 解析符号链接 + 路径必须位于允许目录
    （/var/lib/ghlink、系统临时目录、/tmp、/var/tmp 或用户主目录）内。
    """
    import tempfile

    resolved = os.path.realpath(lock_path)
    if not os.path.isabs(resolved):
        raise ValueError(f"lock path must be absolute: {lock_path}")
    # v0.2.19（李工 8 条⑧）：锁路径基准已改为平台默认目录（_config_base），
    # 白名单同步补 %ProgramData%\ghlink（Windows SYSTEM 可写）与 /etc/ghlink（root）
    allowed_roots = (
        "/var/lib/ghlink",
        "/etc/ghlink",
        os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "ghlink"),
        tempfile.gettempdir(),
        os.path.expanduser("~"),
    )
    for root in allowed_roots:
        root = os.path.realpath(root)
        if resolved == root or resolved.startswith(root + os.sep):
            return resolved
    raise ValueError(f"lock path outside allowed dirs: {resolved}")


@contextmanager
def acquire(lock_path: str, stale_after_sec: int = 600) -> Iterator[bool]:
    """获取锁；成功 yield True，已被持有 yield False（调用方直接退出本轮）。"""
    # SonarCloud S8707：入口统一校验+规范化路径，后续访问全部使用校验后路径
    lock_path = _safe_lock_path(lock_path)
    # Linux/macOS 用 flock 内核锁（进程退出自动释放）
    if sys.platform != "win32":
        try:
            import fcntl

            # 2026-08-17 Bug B 修复：绝对路径（/var/lib/ghlink/）目录可能不存在，先建
            os.makedirs(os.path.dirname(lock_path), exist_ok=True)
            # SonarCloud S5443：O_NOFOLLOW 防符号链接攻击（公共可写目录安全使用）
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(lock_path, flags, 0o644)
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
                # SonarCloud S5443：O_NOFOLLOW 防符号链接攻击（公共可写目录安全使用）
                flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(lock_path, flags, 0o644)
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
                with open(lock_path, encoding="utf-8") as f:
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
                with open(lock_path, encoding="utf-8") as f:
                    pid_str = f.read().split()[0]
                if int(pid_str) == os.getpid():
                    os.unlink(lock_path)
            except (OSError, ValueError, IndexError):
                pass
