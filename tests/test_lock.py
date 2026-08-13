"""防重入文件锁测试。

对应 src/ghlink/lock.py：acquire。
口径（方案草案）：
- 定时跑 + 手动跑不重叠：已被持有 → yield False，调用方直接退出本轮
- 残留锁接管：PID 已死 / 超时（默认 600s）→ 视为残留锁，接管
"""
import os

from ghlink import lock


class TestAcquire:
    def test_acquire_success_yields_true(self, tmp_path):
        with lock.acquire(str(tmp_path / "g.lock")) as got:
            assert got is True

    def test_reentrant_yields_false(self, tmp_path):
        p = str(tmp_path / "g.lock")
        with lock.acquire(p) as first:
            assert first is True
            with lock.acquire(p) as second:
                assert second is False

    def test_release_allows_reacquire(self, tmp_path):
        p = str(tmp_path / "g.lock")
        with lock.acquire(p) as got:
            assert got is True
        with lock.acquire(p) as got:
            assert got is True


class TestStaleLock:
    def test_empty_lock_file_handled(self, tmp_path):
        """空锁文件（0 字节）→ 不崩溃，按可获取处理或明确跳过（赛博复核提醒 1）。"""
        p = tmp_path / "g.lock"
        p.write_text("", encoding="utf-8")
        with lock.acquire(str(p)) as got:
            assert got in (True, False)

    def test_dead_pid_reclaimed(self, tmp_path):
        """锁文件里的 PID 已死 → 接管成功。"""
        p = tmp_path / "g.lock"
        dead_pid = 999999  # 大概率不存在的 PID
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"{dead_pid} {os.getpid() - 1}")
        with lock.acquire(str(p)) as got:
            assert got is True

    def test_expired_lock_reclaimed(self, tmp_path):
        """锁超时（超过 stale_after_sec）→ 接管成功。"""
        p = tmp_path / "g.lock"
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"{os.getpid()} 0")  # 时间戳 0 = 早已过期
        with lock.acquire(str(p), stale_after_sec=600) as got:
            assert got is True
