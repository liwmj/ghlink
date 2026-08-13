"""跨平台文件锁（防重入）：定时跑 + 手动跑不重叠。

实现要点（顾笙）：
- 锁文件内容 = PID + 时间戳；acquire 时检查：
  a) 锁文件不存在 → 获取
  b) 存在且 PID 存活 → 已持有，跳过本轮（不阻塞不排队）
  c) 存在但 PID 已死/超时（如 10min 过期）→ 视为残留锁，接管
- Windows 用 msvcrt.locking 或直接 PID 文件（进程内互斥足够，无需内核锁）
- Linux/macOS 用 fcntl.flock（进程退出自动释放）
"""
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def acquire(lock_path: str, stale_after_sec: int = 600) -> Iterator[bool]:
    """获取锁；成功 yield True，已被持有 yield False（调用方直接退出本轮）。"""
    # TODO(顾笙)
    yield True
