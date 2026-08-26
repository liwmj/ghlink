"""定时任务注册/管理服务（v0.2 阶段 1）。

提供 ghlink enable / disable / status 三命令的跨平台实现：
- enable：注册 1 小时粒度定时任务（v0.2.18 起）（systemd timer / crontab / LaunchDaemon / schtasks）
- disable：移除定时任务
- status：显示当前状态 + 值守状态

权限约定（v0.2 草案）：
- 默认不自启：安装只部署文件，enable 才注册定时任务
- Linux/macOS 需 root；Windows 用 /RL HIGHEST /RU SYSTEM 绕 UAC
- 提权/注册失败：明确报错退出码 2，不静默
"""

import os
import shutil
import sys
import time
from typing import Optional

from . import hosts_manager, platform_adapter, state
from .lock import _pid_alive  # v0.2.17 ⑤：PID 文件兜底存活判定

# v0.4.25（SonarCloud S1192）：ghlink 安装路径常量——多处（wrapper 候选/sudoers
# 模板/PYTHONPATH）共用，防字面量重复超标。
_GHLINK_APP_WRAPPER = "/Applications/ghlink.app/Contents/MacOS/ghlink"
_GHLINK_APP_LIBEXEC = "/Applications/ghlink.app/Contents/libexec"
_GHLINK_BIN = "/usr/local/bin/ghlink"
_GHLINK_HOMEBREW_BIN = "/opt/homebrew/bin/ghlink"
_GHLINK_USR_BIN = "/usr/bin/ghlink"


def _wrapper_candidates() -> tuple:
    """ghlink wrapper 候选路径（含 .app 绝对路径，GUI/launchd PATH 受限场景兜底）。

    v0.4.25（赛博根因 2026-08-26，顾笙实测）：GUI 应用（Finder/LaunchServices 双击）
    PATH 只有 /usr/bin:/bin:/usr/sbin:/sbin，且 relocate 事故后 /usr/local/bin/ghlink
    软链会断——候选列表必须含 .app 内绝对路径，且四处（_python_cmd/_find_wrapper/
    _macos_daemon_command）共用，防 SonarCloud 重复率超标。
    """
    return (
        _GHLINK_APP_WRAPPER,
        _GHLINK_BIN,
        _GHLINK_HOMEBREW_BIN,
        _GHLINK_USR_BIN,
    )


def _find_wrapper() -> Optional[str]:
    """返回第一个存在的 wrapper 绝对路径；无则 which 兜底。

    darwin：.app 内绝对路径优先（GUI/launchd PATH 受限 + relocate 软链断场景，
    v0.4.25 赛博根因）；其他平台：which 优先（保持 v0.2.17 原语义）。
    """
    if sys.platform == "darwin":
        for cand in _wrapper_candidates():
            if os.path.exists(cand):
                return cand
    w = shutil.which("ghlink")
    if w:
        return w
    for cand in _wrapper_candidates():
        if os.path.exists(cand):
            return cand
    return None


def _python_cmd() -> str:
    """值守执行入口：优先 wrapper（带 PYTHONPATH），回退裸 python -m。

    2026-08-17 Bug A 修复（拂晓/顾笙双端实锤）：plist/systemd 裸调
    sys.executable -m ghlink.main 无 PYTHONPATH → ModuleNotFoundError，
    值守 enable 了等于没 enable。wrapper（/usr/local/bin/ghlink 或
    /usr/bin/ghlink）自带 PYTHONPATH，必须优先使用。
    """
    # Windows frozen：windowed 入口静默跑（李工 13:34 定）
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        watch = os.path.join(exe_dir, "ghlink-watch.exe")
        if os.path.exists(watch):
            return f'"{watch}"'
    # 优先 wrapper（含 .app 绝对路径，GUI/launchd PATH 受限场景兜底）
    w = _find_wrapper()
    if w:
        return f'"{w}"'
    # 开发模式回退：裸 python -m（源码/venv 环境 PYTHONPATH 天然可用）
    py = sys.executable or "python3"
    return f'"{py}" -m ghlink.main'


def _install_prefix() -> str:
    """配置文件默认位置（/etc/ghlink 或用户目录）。"""
    if os.name == "posix" and os.geteuid() == 0:
        return "/etc/ghlink"
    return os.path.join(os.path.expanduser("~"), ".ghlink")


def _config_path() -> str:
    """配置文件路径：优先用户/系统默认位置，普通用户回退读系统级配置。

    2026-08-17 Bug D（赛博定案）：_install_prefix() 按 euid 分叉，普通用户
    只找 ~/.ghlink/config.json（通常不存在）→ _state_path() 退化到用户目录
    相对路径，读不到 /var/lib/ghlink/ 的绝对路径状态文件 → 心跳恒判不新鲜
    → 误报「值守: 异常（僵尸）」。修复：非 root 且用户 config 不存在时，
    按可读性回退系统级 config（/usr/local/etc/ghlink → /etc/ghlink），
    让普通用户 status/tray 能拿到绝对路径 state_file（Bug C 的 chmod 0644
    在此链路下才能真正闭环）。

    2026-08-21 v0.2.19（李工 8 条②）：补 /opt/homebrew 候选——Apple Silicon
    brew 前缀是 /opt/homebrew，_ensure_config() 模板候选也同步补。
    """
    primary = os.path.join(_install_prefix(), "config.json")
    if os.path.exists(primary):
        return primary
    if os.name == "posix" and os.geteuid() != 0:
        for cand in (
            # v0.4.3（李工 8 bug 点④ macOS 真机验收）：/etc/ghlink 优先——
            # enable(root) 写入的权威位置，心跳最新；旧残留（brew/旧版）可能指向
            # 绝对路径 state_file 导致 tray 读旧心跳误判僵尸，必须放最后
            "/etc/ghlink/config.json",
            "/opt/homebrew/etc/ghlink/config.json",
            "/usr/local/etc/ghlink/config.json",
        ):
            if os.path.exists(cand) and os.access(cand, os.R_OK):
                return cand
    return primary


def _service_name() -> str:
    return "ghlink"


def _state_path() -> str:
    """状态文件路径：优先从 config.json 的 state_file 字段读取，否则默认 ghlink_status.json。

    与 tray.py 同模式（赛博 08:51 复核指出：config.json 本身无 timestamp 字段，
    直接读 config 会把配置当状态文件，导致心跳恒判不新鲜）。
    2026-08-17 赛博补强（Bug B 根治）：相对路径→相对 config.json 目录解析，
    避免 systemd/LaunchDaemon（cwd=/）与 CLI（cwd=用户目录）读写不一致。
    """
    cfg_path = _config_path()
    st_path = "ghlink_status.json"
    if os.path.exists(cfg_path):
        try:
            import json as _json

            with open(cfg_path, encoding="utf-8") as f:
                cfg = _json.load(f)
            st_path = cfg.get("state_file", "ghlink_status.json")
        except Exception:
            pass
    # 相对路径 → 相对 config.json 所在目录；绝对路径原样返回
    if st_path and not os.path.isabs(st_path):
        st_path = os.path.join(os.path.dirname(os.path.abspath(cfg_path)), st_path)
    return st_path


def enable() -> int:
    """注册定时任务。返回退出码（0=成功，2=权限/错误）。"""
    if not platform_adapter.ensure_privilege():
        print(
            "[ghlink] 错误：需要管理员/root 权限。"
            "请运行 sudo ghlink enable（Linux/macOS）或管理员命令行（Windows）",
            file=sys.stderr,
        )
        return 2
    # 2026-08-17 Bug B 修复：enable 前确保 config 落位（/etc/ghlink/config.json 不存在则复制）
    _ensure_config()
    # v0.4.3（李工 8 bug 点④ macOS 真机验收）：macOS 上旧版 brew/残留 config
    # （/usr/local/etc、/opt/homebrew/etc）与 /etc/ghlink 并存 → state_file 解析不一致
    # （相对 vs 绝对）→ tray 读旧状态文件误判「值守未运行」。enable 时清理并存旧 config：
    # 备份到 /etc/ghlink/backup/ 后删除，只留权威 /etc/ghlink/config.json。
    _cleanup_duplicate_configs()
    # v0.2.19.2（赛博 Windows 严格测试 P1）：升级迁移——旧版 config 的
    # state/lock/backup 可能是 Unix 绝对路径（/var/lib/ghlink/...），在 Windows
    # 上无效 → 状态文件写不进 → 心跳停 + hosts 不落盘。检测到平台无效路径时
    # 备份旧 config 并仅修正路径字段（保留 notify 等用户配置）。
    _migrate_legacy_paths()
    # v0.4.0（李工 12:35 点 3）：enable 时检测 hosts 段落外预存的 GitHub 生态域名条目——
    # first-match-wins 下可能与 ghlink 写入冲突。命中则告警 + 自动备份（用户记录不动）。
    try:
        dupes = hosts_manager.detect_external_dupes()
        if dupes:
            print(
                "[ghlink] 警告：hosts 中检测到段落外预存的 GitHub 域名条目"
                "（first-match-wins 下可能遮蔽 ghlink 写入）："
            )
            for d, ip in list(dupes.items())[:10]:
                print(f"  - {d} -> {ip}")
            if len(dupes) > 10:
                print(f"  ... 等共 {len(dupes)} 条")
            # v0.4.2（拂晓复测发现）：backup_hosts 默认 "backup" 相对 cwd → 落点漂移。
            # 改为按 config 目录解析平台化路径（与状态/锁文件同源，main._backup_dir 同语义）
            cfg_path = _config_path()
            backup_dir = os.path.join(os.path.dirname(os.path.abspath(cfg_path)), "backup")
            backup = platform_adapter.backup_hosts(backup_dir)
            print(
                f"[ghlink] 已自动备份原 hosts（{backup or '备份失败'}），"
                "用户记录未改动；如需自定义请编辑后重新 enable"
            )
    except Exception:
        pass  # 冲突检测失败不阻断 enable
    try:
        if sys.platform == "win32":
            return _enable_windows()
        elif sys.platform == "darwin":
            return _enable_macos()
        else:
            return _enable_linux()
    except Exception as exc:
        print(f"[ghlink] enable 失败: {exc}", file=sys.stderr)
        return 2


def _cleanup_duplicate_configs() -> None:
    """清理并存旧 config，根治状态文件路径分裂（v0.4.3）。

    2026-08-23 李工 8 bug 点④ macOS 真机验收（顾笙诊断）：macOS 上
    /etc/ghlink/config.json（state_file 相对路径，LaunchDaemon 写）与
    /usr/local/etc/ghlink/config.json（state_file 绝对路径 /var/lib/ghlink/...，
    旧版 brew 残留）并存 → tray 的 _config_path() 解析到旧文件读旧心跳
    → 误判「值守未运行」（实际值守正常）。

    修复：enable（root）时检测 /usr/local/etc/ghlink、/opt/homebrew/etc/ghlink
    下的 config.json，备份到权威目录 backup/ 后删除，只留 /etc/ghlink/config.json。
    """
    if os.name != "posix" or os.geteuid() != 0:
        return
    authority = "/etc/ghlink/config.json"
    if not os.path.exists(authority):
        return
    import shutil as _shutil
    import time as _t

    backup_dir = "/etc/ghlink/backup"
    for old in (
        "/usr/local/etc/ghlink/config.json",
        "/opt/homebrew/etc/ghlink/config.json",
    ):
        if not os.path.exists(old):
            continue
        try:
            os.makedirs(backup_dir, exist_ok=True)
            stamp = _t.strftime("%Y%m%d%H%M%S")
            dst = os.path.join(
                backup_dir,
                f"config.legacy.{os.path.basename(os.path.dirname(old))}.{stamp}",
            )
            _shutil.copy2(old, dst)
            os.unlink(old)
            print(f"[ghlink] 已清理并存旧 config（备份至 {dst}），统一使用 /etc/ghlink/config.json")
        except OSError as exc:
            print(f"[ghlink] 清理旧 config {old} 失败（跳过，不影响 enable）: {exc}")


def _ensure_config() -> None:
    """确保配置文件存在：目标 _config_path()，缺失则从 config.example.json 复制。
    2026-08-17 拂晓/顾笙双端实锤 Bug B：deb/brew 装完 /etc/ghlink/config.json
    可能不存在（brew 不落配置、deb 路径不一致），LaunchDaemon/systemd 裸跑
    直接读不到 → 值守僵尸。enable 前兜底复制，保证注册即能跑。
    2026-08-17 Bug D（赛博定案）：复制后 chmod 0644 + 目录 0755，
    让普通用户 status/tray 可读系统级 config（配合 _config_path() fallback），
    Bug C 的 0644 状态文件才能真正闭环。
    """
    import shutil as _shutil

    cfg_path = _config_path()
    if not os.path.exists(cfg_path):
        # 候选模板：当前目录 / 仓库 / 安装包 libexec / 系统 share（v0.2.19 补 /opt/homebrew）
        candidates = [
            os.path.join(os.getcwd(), "config.example.json"),
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "config.example.json",
            ),
            "/opt/homebrew/Cellar/ghlink/libexec/config.example.json",
            "/usr/local/Cellar/ghlink/libexec/config.example.json",
            "/usr/share/ghlink/config.example.json",
            "/usr/lib/ghlink/config.example.json",
        ]
        for src in candidates:
            if os.path.exists(src):
                os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
                _shutil.copy(src, cfg_path)
                print(f"[ghlink] 已生成默认配置: {cfg_path}")
                break
        else:
            print(
                f"[ghlink] 警告：找不到 config.example.json 模板，跳过配置落位（{cfg_path}）",
                file=sys.stderr,
            )
    # Bug D：配置与目录权限对普通用户可读（enable 以 root 跑，落位后放开读权限）
    if os.path.exists(cfg_path):
        try:
            os.chmod(cfg_path, 0o644)  # NOSONAR
            os.chmod(os.path.dirname(cfg_path), 0o755)  # NOSONAR
        except OSError:
            pass


def _platform_valid_path(path: str) -> bool:
    """路径在当前平台是否有效。

    Windows：Unix 绝对路径（/var/lib/ghlink/...，非盘符开头）无效；
    POSIX：相对路径有效（会按 config 目录解析），绝对路径需 / 开头。
    """
    if not path:
        return True
    if sys.platform == "win32":
        import re as _re

        # 盘符开头（C:\）或 UNC（\\）或相对路径（无盘符、非 / 开头）有效
        if _re.match(r"^[A-Za-z]:[\\/]", path) or path.startswith("\\"):
            return True
        return not path.startswith("/")
    return True


def _migrate_legacy_paths() -> None:
    """升级迁移：修正 config 中平台无效的 state/lock/backup 路径字段。

    v0.2.19.2（赛博 Windows 严格测试 P1）：旧版（<=0.2.18）config.example.json
    模板是 Unix 绝对路径 /var/lib/ghlink/...，Windows 用户升级后 enable 不覆盖
    已有 config → 首轮用旧配置 → 状态/锁路径无效 → hosts 不落盘 + 心跳停。
    策略：备份旧 config（config.json.bak-<ts>），仅把平台无效的路径字段改为
    相对路径（按 config 目录解析，三平台通用），其余字段（notify 等）保留。
    """
    cfg_path = _config_path()
    if not cfg_path or not os.path.exists(cfg_path):
        return
    try:
        import json as _json
        import shutil as _shutil

        with open(cfg_path, encoding="utf-8") as f:
            cfg = _json.load(f)
        fixed = {}
        for key in ("state_file", "lock_file", "hosts_backup_dir"):
            val = cfg.get(key)
            if val and not _platform_valid_path(str(val)):
                fixed[key] = val
                cfg[key] = {  # 相对路径按 config 目录解析（_resolve_rel 语义）
                    "state_file": "ghlink_status.json",
                    "lock_file": "ghlink.lock",
                    "hosts_backup_dir": "backup",
                }[key]
        if not fixed:
            return
        # 备份旧 config（可恢复）
        ts = time.strftime("%Y%m%d%H%M%S")
        bak = f"{cfg_path}.bak-{ts}"
        _shutil.copy2(cfg_path, bak)
        with open(cfg_path, "w", encoding="utf-8") as f:
            _json.dump(cfg, f, ensure_ascii=False, indent=2)
        for key, old_val in fixed.items():
            print(
                f"[ghlink] 升级迁移：{key} 旧值 {old_val} 在当前平台无效，"
                f"已改为 {cfg[key]}（旧配置备份 {bak}）"
            )
    except Exception as exc:
        print(f"[ghlink] 升级迁移跳过：{exc}", file=sys.stderr)


def disable() -> int:
    """移除定时任务，保留 hosts 段落与配置（李工 2026-08-22 19:31 终裁：disable=暂停）。
    返回退出码（0=成功，2=权限/错误）。"""
    if not platform_adapter.ensure_privilege():
        print(
            "[ghlink] 错误：需要管理员/root 权限。"
            "请运行 sudo ghlink disable（Linux/macOS）或管理员命令行（Windows）",
            file=sys.stderr,
        )
        return 2
    try:
        if sys.platform == "win32":
            rc = _disable_windows()
        elif sys.platform == "darwin":
            rc = _disable_macos()
        else:
            rc = _disable_linux()
        if rc == 0:
            print(
                "[ghlink] 已停用值守：保留最后写入的 hosts IP 与配置，不再自动更新。"
                "如需清理 hosts 段落或彻底卸载，请用 ghlink uninstall"
            )
        return rc
    except Exception as exc:
        print(f"[ghlink] disable 失败: {exc}", file=sys.stderr)
        return 2


# v0.4.14：二进制缺失时手动清理指引（_uninstall_self_elevate 使用）
_MANUAL_CLEANUP_STEPS = (
    "sudo rm -f /etc/sudoers.d/ghlink",
    "sudo launchctl bootout system /Library/LaunchDaemons/com.ghlink.plist "
    "2>/dev/null; sudo rm -f /Library/LaunchDaemons/com.ghlink.plist",
    "sudo sed -i '' '/# ghlink Start/,/# ghlink End/d' /etc/hosts",
    "sudo rm -rf /usr/local/etc/ghlink /opt/homebrew/etc/ghlink ~/.ghlink /var/lib/ghlink",
)


def _uninstall_self_elevate() -> Optional[int]:
    """非 root 时以 sudo 重跑本命令（root 后正常执行，无死循环）。

    返回退出码；已是 root（无需提权）返回 None。"""
    if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() == 0:
        return None
    import subprocess as _sp

    # v0.4.15（验收发现）：brew 子进程 PATH 下 which 失败、python -m 使 argv[0]
    # 变成 main.py 路径——均不匹配 sudoers NOPASSWD → 确定性候选列表找 wrapper
    # v0.4.25：统一走 _find_wrapper()（含 .app 绝对路径，GUI/launchd PATH 受限兜底）
    exe = _find_wrapper()
    # v0.4.14（review 建议）：绝对路径 /usr/bin/sudo 收 PATH 注入面；
    # 二进制已删（卸载中途/手动清理场景）时给出手动清理指引，不静默失败
    if not exe or not os.path.exists(exe):
        print(
            "[ghlink] 未找到 ghlink 可执行文件（可能已被移除），无法自动卸载。请手动清理以下残留：",
            file=sys.stderr,
        )
        for step in _MANUAL_CLEANUP_STEPS:
            print(f"  {step}", file=sys.stderr)
        return 2
    try:
        r = _sp.run(["/usr/bin/sudo", exe, "uninstall"], check=False)  # NOSONAR
        return r.returncode
    except Exception as exc:
        print(
            f"[ghlink] 卸载需要 root 权限，提权失败（{exc}），请手动运行: sudo ghlink uninstall",
            file=sys.stderr,
        )
        return 2


def uninstall() -> int:
    """卸载清理（李工 2026-08-22 19:31 终裁：uninstall=彻底删除）。

    = disable（停任务）+ remove_block（还原 hosts）+ 删配置目录（当前平台）。
    返回退出码（0=成功，2=权限/错误）。

    v0.4.14（Cask 卸载事故修复）：非 root 时自提权重跑——brew cask uninstall
    不再用 sudo -E 包装（macOS 默认 sudoers 未开 setenv，-E 必被拒），改由本命令
    内部普通 sudo 提权（有 NOPASSWD 窄放行免密、无则交互输密码）。卸载同时自清
    ghlink 关联的 sudoers 规则与运行残留（此前需全手动清）。"""
    # 自提权：非 root 时以 sudo 重跑本命令（root 后走正常流程，无死循环）
    elevated = _uninstall_self_elevate()
    if elevated is not None:
        return elevated
    rc = disable()
    if rc != 0:
        return rc
    # 还原 hosts（删 ghlink 段落 + 恢复基线）
    if hosts_manager.remove_block():
        print("[ghlink] 已还原 hosts（移除 ghlink 段落）")
    else:
        print("[ghlink] 警告：hosts 段落移除失败（权限？），请手动检查", file=sys.stderr)
    # 删除配置目录（当前平台生效的配置/状态/缓存）
    cfg_path = _config_path()
    cfg_dir = os.path.dirname(os.path.abspath(cfg_path)) if cfg_path else ""
    for d in (cfg_dir,):
        if d and os.path.isdir(d) and os.path.basename(d) in ("ghlink", ".ghlink"):
            import shutil as _sh

            _sh.rmtree(d, ignore_errors=True)
            print(f"[ghlink] 已删除配置目录: {d}")
    # v0.4.14：清理 ghlink 关联的系统残留（Cask 卸载事故暴露，此前需全手动）
    _cleanup_uninstall_residue()
    return 0


def _cleanup_uninstall_residue() -> None:
    """卸载残留清理（v0.4.14，root 上下文执行）。

    - /etc/sudoers.d/ghlink：李工 v0.4.5 决策点 2 放行的 NOPASSWD/!env_reset 规则，
      卸载必须自清（内容含 ghlink 才删，防误删用户自建规则）
    - ~/.ghlink：托盘 PID/用户态残留（v0.4.14 事故中 ghlink-tray.pid 残留根因）
    - /var/lib/ghlink：root 状态目录（/etc/ghlink 场景）
    - /usr/local/etc/ghlink、/opt/homebrew/etc/ghlink：旧 brew 配置残留
    """
    import shutil as _sh

    sudoers_d = "/etc/sudoers.d/ghlink"
    if os.path.exists(sudoers_d):
        try:
            with open(sudoers_d, encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if "ghlink" in content:
                os.unlink(sudoers_d)
                print(f"[ghlink] 已清理 sudoers 规则: {sudoers_d}")
            else:
                print(
                    f"[ghlink] 跳过 {sudoers_d}（内容不含 ghlink，疑似非本工具规则）",
                    file=sys.stderr,
                )
        except OSError as exc:
            print(f"[ghlink] 警告：清理 {sudoers_d} 失败: {exc}", file=sys.stderr)
    for d in (
        os.path.expanduser("~/.ghlink"),
        "/var/lib/ghlink",
        "/usr/local/etc/ghlink",
        "/opt/homebrew/etc/ghlink",
    ):
        if os.path.isdir(d):
            _sh.rmtree(d, ignore_errors=True)
            print(f"[ghlink] 已清理残留目录: {d}")


def status() -> int:
    """显示当前状态 + 值守状态。始终返回 0。"""
    st = state.load(_state_path())
    cur_ip = st.get("current_ip") or _hosts_github_ip() or _dns_github_ip()
    print("=== ghlink status ===")
    print(f"状态: {st.get('state', 'normal')}")
    print(f"当前IP: {cur_ip or '-'}")
    print(f"失败计数: {st.get('probe', {}).get('consecutive_failures', 0)}")
    print(f"最近错误: {st.get('last_error') or '-'}")
    # v0.4.0：多源回退候选来源（candidate_sources 状态字段，degraded 可追溯）
    cs = st.get("candidate_sources")
    if cs:
        cs_txt = " ".join(f"{k}={v}" for k, v in sorted(cs.items()))
        print(f"候选来源: {cs_txt}")
    switched = st.get("last_switched_at") or st.get("switched_at")  # v0.2.18 方案④：兼容旧字段
    print(
        "上次切换: "
        + (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(switched)) if switched else "-")
    )
    # 值守判断（2026-08-17 李工新口径：值守独立于托盘）
    # 主判据=平台任务注册；次判据=心跳新鲜；托盘进程仅展示层
    print(f"值守: {_watch_status_text()}")
    history = st.get("history") or []
    if history:
        print("最近记录:")
        for h in history[-5:]:
            print(
                f"  - {h.get('time', '')} {h.get('domain', '')} → {h.get('ip', '')} "
                f"({h.get('trigger', '')})"
            )
    return 0


def _watch_status_text() -> str:
    """值守状态细分文本（2026-08-17 李工新口径：值守独立于托盘）。

    主判据=平台任务注册；次判据=心跳新鲜。
    注册+心跳新=已启用；注册但心跳停=僵尸（异常）；未注册=未启用。
    托盘进程降为展示层，附「托盘: 运行中/未运行」辅助行。

    v0.2.19（李工 8 条②）：注册但心跳停时，区分「首轮执行中」（从未心跳）
    与「疑似僵尸」（历史有心跳但已停）——enable 后立即触发第一轮，首轮
    探测期间查 status 不再误报僵尸。
    """
    try:
        registered = _is_registered()
        hb = _heartbeat_fresh()
        tray = _tray_alive()
        if registered and hb:
            base = "已启用（值守注册 + 心跳正常）"
        elif registered and not hb:
            if _heartbeat_never():
                base = "已启用（首轮执行中，心跳待写入）"
            else:
                base = "异常（值守已注册但心跳已停，疑似僵尸）"
        else:
            base = "未启用（运行 ghlink enable 开启值守）"
        return f"{base}｜托盘: {'运行中' if tray else '未运行'}"
    except Exception:
        return "未知"


def _dns_github_ip() -> str:
    """系统 DNS 解析 github.com 的 IP（hosts 无条目时兜底，v0.2.9 补全链路）。"""
    try:
        import socket

        infos = socket.getaddrinfo("github.com", None, socket.AF_INET)
        return str(infos[0][4][0])
    except Exception:
        return ""


def _hosts_github_ip() -> str:
    """hosts 里当前生效的 github.com IP（未切换时兜底显示，与托盘口径一致 v0.2.8）。"""
    try:
        import os as _os

        if sys.platform == "win32":
            root = _os.environ.get("SYSTEMROOT", r"C:\Windows")
            hosts_path = _os.path.join(root, "System32", "drivers", "etc", "hosts")
        else:
            hosts_path = "/etc/hosts"
        with open(hosts_path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2 and "github.com" in parts[1:]:
                    ip = parts[0]
                    if ip not in ("127.0.0.1", "::1", "0.0.0.0"):
                        return ip
    except Exception:
        pass
    return ""


def _is_autostart() -> bool:
    """检测开机自启动是否已注册（Windows Run key / macOS LaunchAgent / Linux .desktop）。"""
    try:
        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
            ) as key:
                winreg.QueryValueEx(key, "ghlink-tray")
                return True
        elif sys.platform == "darwin":
            return os.path.exists(
                os.path.expanduser("~/Library/LaunchAgents/com.ghlink.tray.plist")
            )
        else:
            return os.path.exists(os.path.expanduser("~/.config/autostart/ghlink-tray.desktop"))
    except Exception:
        return False


def _enable_autostart() -> bool:
    """注册开机自启动（托盘随登录启动）。用户级，无需提权。"""
    try:
        if sys.platform == "win32":
            import winreg

            exe = os.path.join(os.path.dirname(sys.executable), "ghlink-tray.exe")
            if not os.path.exists(exe):
                exe = sys.executable
            with winreg.CreateKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
            ) as key:
                winreg.SetValueEx(key, "ghlink-tray", 0, winreg.REG_SZ, f'"{exe}"')
            return True
        elif sys.platform == "darwin":
            plist_dir = os.path.expanduser("~/Library/LaunchAgents")
            os.makedirs(plist_dir, exist_ok=True)
            plist = os.path.join(plist_dir, "com.ghlink.tray.plist")
            # v0.4.25（赛博根因 2026-08-26，顾笙实测）：GUI 环境 PATH 无 /usr/local/bin，
            # shutil.which("ghlink") 可能找不到 → 回退 sys.executable 裸 python →
            # LaunchAgent 拉起即 ModuleNotFoundError（与 LaunchDaemon 同病根）。
            # 统一 _find_wrapper()：.app 内 wrapper（自带 PYTHONPATH + PATH 补全）优先。
            exe = _find_wrapper() or sys.executable
            with open(plist, "w", encoding="utf-8") as f:
                # v0.4.27（李工 13:33 实测：退出托盘后二次打开 APP 托盘不回来）：
                # 原 plist 仅 RunAtLoad（登录拉一次），进程退出后 launchctl 不重启。
                # 加 KeepAlive {SuccessfulExit: false}：崩溃/被杀自动拉起，
                # 用户显式退出（正常 exit 0）不拉起，语义不冲突。
                f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.ghlink.tray</string>
  <key>ProgramArguments</key><array><string>{exe}</string><string>tray</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key><false/>
  </dict>
  <key>LimitLoadToSessionType</key><string>Aqua</string>
</dict></plist>
""")
            import subprocess as _sp

            # 赛博 09:56 问题 A：plist 照写（自启动注册必须成功），
            # 已在跑时不重复 load（避免多实例），靠单实例锁兜底
            if not _tray_alive(exclude_pid=os.getpid()):
                _sp.run(["launchctl", "load", plist], check=False)
            else:
                print("[ghlink] 托盘已在运行，仅注册自启动（不重复拉起）")
            return True
        else:
            autostart_dir = os.path.expanduser("~/.config/autostart")
            os.makedirs(autostart_dir, exist_ok=True)
            desktop = os.path.join(autostart_dir, "ghlink-tray.desktop")
            with open(desktop, "w", encoding="utf-8") as f:
                f.write("[Desktop Entry]\nType=Application\nName=ghlink tray\nExec=ghlink tray\n")
            return True
    except Exception:
        return False


def _disable_autostart() -> bool:
    """移除开机自启动。"""
    try:
        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.DeleteValue(key, "ghlink-tray")
            return True
        elif sys.platform == "darwin":
            plist = os.path.expanduser("~/Library/LaunchAgents/com.ghlink.tray.plist")
            import subprocess as _sp

            _sp.run(["launchctl", "unload", plist], check=False)
            if os.path.exists(plist):
                os.remove(plist)
            return True
        else:
            desktop = os.path.expanduser("~/.config/autostart/ghlink-tray.desktop")
            if os.path.exists(desktop):
                os.remove(desktop)
            return True
    except Exception:
        return False


def _is_registered() -> bool:
    """平台定时任务是否已注册（enable/disable 幂等判断用，旧口径）。"""
    try:
        if sys.platform == "win32":
            r = platform_adapter._run_cmd(["schtasks", "/Query", "/TN", _service_name()])
            return r
        elif sys.platform == "darwin":
            return os.path.exists("/Library/LaunchDaemons/com.ghlink.plist")
        else:
            # Linux: systemd timer 或 crontab
            if os.path.exists("/etc/systemd/system/ghlink.timer"):
                return True
            out = platform_adapter._run_cmd_output(["crontab", "-l"])
            return "ghlink.main" in (out or "")
    except Exception:
        return False


def _tray_single_instance() -> bool:
    """单实例锁：已有托盘实例则返回 True（本次应退出）。

    - Windows：命名互斥体（CreateMutex）——PyInstaller onefile 下 exe 运行时
      是「引导进程 + Python 子进程」两个同名进程，tasklist 排除自身 PID 仍会
      误判引导进程为已有实例（李工 14:40 反馈：Windows 托盘闪退根因）。
      命名互斥体是 Windows 标准单实例方案，onefile 下可靠。
    - macOS/Linux：pgrep/tasklist 排除自身 PID。
    """
    if sys.platform == "win32":
        try:
            import ctypes

            global _TRAY_MUTEX_HANDLE
            # Global\ 前缀：跨会话可见（防快速用户切换/RDP 双会话单实例失效）
            handle = ctypes.windll.kernel32.CreateMutexW(
                None, False, "Global\\ghlink-tray-singleton"
            )
            if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
                return True
            # 模块级持有句柄（防 GC 释放互斥体导致锁失效）
            _TRAY_MUTEX_HANDLE = handle
            return False
        except Exception:
            return False
    return _tray_alive(exclude_pid=os.getpid())


# Windows 命名互斥体句柄（模块级持有，防 GC）
_TRAY_MUTEX_HANDLE = None


def _tray_pid_file() -> str:
    """托盘 PID 文件路径（v0.2.17 ⑤，赛博定案：PID 文件兜底）。

    macOS 上 pgrep 正则会因 detach 后命令行形态（-m ghlink.main tray）
    匹配不一致导致误判「托盘未运行」——改用 PID 文件：托盘启动时写
    自己的 PID，_tray_alive() 优先读 PID 文件 + 进程存活检查，pgrep 降为兜底。
    普通用户进程 → 放用户主目录（/var/lib/ghlink 属 root 不可写）。
    """
    return os.path.join(os.path.expanduser("~"), ".ghlink", "ghlink-tray.pid")


def _write_tray_pid() -> None:
    """托盘启动时写入自身 PID（⑤ 配套）。"""
    try:
        pid_file = _tray_pid_file()
        os.makedirs(os.path.dirname(pid_file), exist_ok=True)
        with open(pid_file, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def _tray_alive(exclude_pid: int = 0) -> bool:
    """托盘进程是否存活（展示层用）。

    macOS 优先 PID 文件（v0.2.17 ⑤，detach 后 pgrep 正则不可靠），
    pgrep 兜底；Windows tasklist；Linux 无托盘。
    """
    try:
        if sys.platform == "win32":
            out = platform_adapter._run_cmd_output(
                ["tasklist", "/FO", "CSV", "/FI", "IMAGENAME eq ghlink-tray.exe"]
            )
            if "ghlink-tray.exe" not in (out or ""):
                return False
            if exclude_pid:
                import csv as _csv
                import io as _io

                for row in _csv.reader(_io.StringIO(out or "")):
                    if len(row) >= 2 and row[0].strip('"') == "ghlink-tray.exe":
                        try:
                            if int(row[1]) != exclude_pid:
                                return True
                        except ValueError:
                            pass
                return False
            return True
        elif sys.platform == "darwin":
            # v0.2.17 ⑤：PID 文件优先（detach 后 pgrep -f 正则与命令行形态不一致）
            pid_file = _tray_pid_file()
            try:
                if os.path.exists(pid_file):
                    with open(pid_file, encoding="utf-8") as f:
                        pid = int(f.read().strip())
                    if pid > 0 and pid != exclude_pid and _pid_alive(pid):
                        return True
                    # v0.4.23（赛博根因 2026-08-26）：PID 文件残留但进程已死
                    # （crash 后锁没释放）→ 删残留文件，stale 锁自动释放
                    try:
                        os.unlink(pid_file)
                    except OSError:
                        pass
            except (OSError, ValueError):
                try:
                    os.unlink(pid_file)
                except OSError:
                    pass
            # 兜底：pgrep（PID 文件缺失/损坏时）
            out = platform_adapter._run_cmd_output(["pgrep", "-f", "ghlink\\.main tray"])
            pids = [int(x) for x in (out or "").split() if x.strip().isdigit()]
            if exclude_pid:
                pids = [p for p in pids if p != exclude_pid]
            return bool(pids)
        return False
    except Exception:
        return False


def _heartbeat_fresh(max_age_sec: int = 5400) -> bool:
    """状态文件心跳是否新鲜（探测 1 小时粒度，v0.2.18 宽限 90 分钟）。

    状态文件由 run() 每轮探测后 save 更新 timestamp；心跳停 = 探测循环没在跑。
    """
    try:
        st = state.load(_state_path())
        ts = st.get("timestamp") or ""
        if not ts:
            return False
        t = time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
        age = time.time() - time.mktime(t)
        return 0 <= age <= max_age_sec
    except Exception:
        return False


def _heartbeat_never() -> bool:
    """状态文件是否从未写入心跳（timestamp 为空）→ 首轮尚未完成。

    v0.2.19（李工 8 条②）：enable 后立即触发第一轮，但首轮探测（8 域名×
    timeout 15s）需要时间——此时查 status 心跳必停，误报「僵尸」吓人。
    用「从未心跳」区分：首轮执行中 vs 真僵尸（历史有心跳但已停）。
    """
    try:
        st = state.load(_state_path())
        return not (st.get("timestamp") or "")
    except Exception:
        return True


def _is_enabled() -> bool:
    """值守是否真正在跑（2026-08-17 李工新口径：值守独立于托盘）。

    - 主判据：平台任务注册（enable 即值守，全平台统一）
    - 次判据：状态文件心跳新鲜（≤3 分钟）
    - 注册但心跳停 = 僵尸（异常，不算启用）
    - 托盘进程降为展示层（托盘在但未注册=提示启动值守）
    """
    try:
        if not _is_registered():
            return False
        return _heartbeat_fresh()
    except Exception:
        return False


def _enable_linux() -> int:
    """Linux：优先 systemd timer，回退 crontab。

    v0.2.19（李工 8 条②）：注册完成后立即触发第一轮——
    systemd 直接 start oneshot service；crontab 直接跑一次，不等整点。
    """
    if os.path.exists("/run/systemd/system"):
        unit = f"""[Unit]
Description=ghlink GitHub connectivity self-healing
After=network.target

[Service]
Type=oneshot
ExecStart={_python_cmd()} {_config_path()}

[Install]
WantedBy=multi-user.target
"""
        timer = """[Unit]
Description=ghlink hourly timer (v0.2.18: 探测 1 小时粒度)

[Timer]
OnCalendar=hourly
AccuracySec=30s

[Install]
WantedBy=timers.target
"""
        with open("/etc/systemd/system/ghlink.service", "w", encoding="utf-8") as f:
            f.write(unit)
        with open("/etc/systemd/system/ghlink.timer", "w", encoding="utf-8") as f:
            f.write(timer)
        if not platform_adapter._run_cmd(["systemctl", "daemon-reload"]):
            return 2
        if not platform_adapter._run_cmd(["systemctl", "enable", "--now", "ghlink.timer"]):
            return 2
        # v0.2.19：注册完立即跑第一轮（不等 OnCalendar 整点）
        # v0.2.19.1（拂晓 Linux 严格测试 #1）：--no-block 异步触发——
        # oneshot 同步等待在 degraded 环境单轮 2-3 分钟且 exit 1 会导致
        # start 报失败误导用户；注册成功即成功，首轮异步跑
        platform_adapter._run_cmd(["systemctl", "start", "--no-block", "ghlink.service"])
        print("[ghlink] 已启用值守并异步触发第一轮（systemd timer，1 小时粒度）")
        return 0
    # crontab 回退
    line = f"0 * * * * {_python_cmd()} {_config_path()} >> /var/log/ghlink.log 2>&1"
    crontab = platform_adapter._run_cmd_output(["crontab", "-l"]) or ""
    if line not in crontab:
        new = crontab.rstrip("\n") + "\n" + line + "\n"
        tmp = "/tmp/ghlink.crontab"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new)
        if not platform_adapter._run_cmd(["crontab", tmp]):
            return 2
        os.unlink(tmp)
    # v0.2.19：注册完立即跑第一轮（不等整点）
    import subprocess as _sp

    try:
        _sp.run(["/bin/sh", "-c", line], timeout=300, check=False)
    except Exception:
        pass
    print("[ghlink] 已启用值守并立即执行第一轮（crontab，1 小时粒度）")
    return 0


def _disable_linux() -> int:
    if os.path.exists("/etc/systemd/system/ghlink.timer"):
        platform_adapter._run_cmd(["systemctl", "disable", "--now", "ghlink.timer"])
        for p in ("/etc/systemd/system/ghlink.timer", "/etc/systemd/system/ghlink.service"):
            if os.path.exists(p):
                os.unlink(p)
        platform_adapter._run_cmd(["systemctl", "daemon-reload"])
        print("[ghlink] 已停用值守（systemd timer 移除）")
        return 0
    crontab = platform_adapter._run_cmd_output(["crontab", "-l"]) or ""
    lines = [ln for ln in crontab.splitlines() if "ghlink.main" not in ln]
    tmp = "/tmp/ghlink.crontab"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    if not platform_adapter._run_cmd(["crontab", tmp]):
        return 2
    os.unlink(tmp)
    print("[ghlink] 已停用值守（crontab 条目移除）")
    return 0


def _ensure_sudoers_macos() -> None:
    """v0.4.19（李工：装了就能用，拒绝手动配置）：enable 以 root 运行时自动写回 sudoers。

    背景：v0.4.5 起 sudoers NOPASSWD 靠人工放行，pkg 重装/卸载自清后丢失，
    托盘「开启值守」sudo -n 被拒（22:11 实测铁证）。enable 本就是 root 跑，
    顺手幂等写回：装机即用、重装不丢、无需任何手动命令。
    """
    if sys.platform != "darwin":
        return
    sudoers_d = "/etc/sudoers.d/ghlink"
    try:
        if os.path.exists(sudoers_d):
            with open(sudoers_d, encoding="utf-8", errors="ignore") as f:
                if "ghlink" in f.read():
                    return  # 已有规则，幂等跳过
        # sudo 下 getuser()=root，需从 SUDO_USER 取原用户
        import getpass

        user = os.environ.get("SUDO_USER") or getpass.getuser()
        content = (
            "# ghlink 托盘提权窄放行（v0.4.19 自动写入，装机即用）\n"
            f"{user} ALL=(root) NOPASSWD: {_GHLINK_BIN}\n"
            f'Defaults!{_GHLINK_BIN} env_keep += "GH_TOKEN"\n'
            f"Defaults!{_GHLINK_BIN} env_keep += "
            '"HTTP_PROXY HTTPS_PROXY NO_PROXY ALL_PROXY"\n'
        )
        tmp = sudoers_d + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(tmp, 0o440)
        os.replace(tmp, sudoers_d)
        print(f"[ghlink] 已自动写入 sudoers 提权规则: {sudoers_d}")
    except Exception as exc:
        print(f"[ghlink] 警告：sudoers 自动写入失败: {exc}", file=sys.stderr)


def _ensure_macos_system_components() -> bool:
    """v0.5.x（李工 14:36「装两个文件离谱」）：app 首启自装系统组件——
    软链 + sudoers + LaunchDaemon 模板一次性装好（弹一次管理员授权）。

    手动安装收敛为：dmg 拖一个 app 进 /Applications，首次运行自动引导，
    不再需要用户手动装第二个 pkg。幂等：已就位直接返回 True。
    """
    if sys.platform != "darwin":
        return True
    app = "/Applications/ghlink.app/Contents/MacOS/ghlink"
    # 已就位检查：软链有效 + sudoers 存在 + LaunchDaemon 模板在
    ok = (
        os.path.islink(_GHLINK_BIN)
        and os.path.realpath(_GHLINK_BIN) == app
        and os.path.exists("/etc/sudoers.d/ghlink")
    )
    if ok:
        return True
    # 缺组件：弹一次管理员授权执行安装脚本（软链 + sudoers + daemon 模板）
    import getpass
    import subprocess as _sp

    sudoers_d = "/etc/sudoers.d/ghlink"
    user = os.environ.get("SUDO_USER") or getpass.getuser()
    script = (
        f"ln -sfn '{app}' {_GHLINK_BIN}; "
        f"mkdir -p /etc/ghlink /usr/local/etc/ghlink; "
        f"cp -n '{_GHLINK_APP_LIBEXEC}/config.json' "
        f"/usr/local/etc/ghlink/config.json 2>/dev/null || true; "
        f"cat > {sudoers_d} <<'EOS'\n"
        f"# ghlink 托盘提权窄放行（app 首启自动写入）\n"
        f"{user} ALL=(root) NOPASSWD: {_GHLINK_BIN}\n"
        f'Defaults!{_GHLINK_BIN} env_keep += "GH_TOKEN"\n'
        f'Defaults!{_GHLINK_BIN} env_keep += "HTTP_PROXY HTTPS_PROXY NO_PROXY ALL_PROXY"\n'
        f"EOS\n"
        f"chmod 0440 {sudoers_d}"
    )
    try:
        r = _sp.run(
            [
                "osascript",
                "-e",
                'do shell script "'
                + script.replace('"', '\\"')
                + '" with administrator privileges',
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if r.returncode == 0:
            print("[ghlink] 系统组件首启自装完成（软链 + sudoers + daemon 模板）")
            return True
        print(f"[ghlink] 系统组件自装失败（用户取消或错误）: {r.stderr.strip()}", file=sys.stderr)
    except Exception as exc:
        print(f"[ghlink] 系统组件自装异常: {exc}", file=sys.stderr)
    return False


def _enable_macos() -> int:
    _ensure_sudoers_macos()  # v0.4.19：root 运行自动写回 sudoers，装机即用
    # v0.4.25（赛博根因 2026-08-26，顾笙实测）：relocate 事故后 /usr/local/bin/ghlink
    # 软链断 → _python_cmd() 找不到 wrapper → 回退裸 python -m → ModuleNotFoundError
    # → daemon 挂（runs=2/last exit=1）。修复：ProgramArguments 用 .app 内绝对路径
    # wrapper（不依赖软链），并显式注入 PYTHONPATH（.app libexec + vendor）双保险。
    daemon_cmd = _macos_daemon_command()
    py_path = _macos_pythonpath()
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.ghlink.daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>{daemon_cmd}</string>
        <string>{_config_path()}</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key><string>{py_path}</string>
    </dict>
    <key>StartInterval</key><integer>3600</integer>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>/var/log/ghlink.log</string>
    <key>StandardErrorPath</key><string>/var/log/ghlink.log</string>
</dict>
</plist>
"""
    with open("/Library/LaunchDaemons/com.ghlink.plist", "w", encoding="utf-8") as f:
        f.write(plist)
    if not platform_adapter._run_cmd(
        ["launchctl", "load", "/Library/LaunchDaemons/com.ghlink.plist"]
    ):
        return 2
    # v0.2.19（李工 8 条②）：注册完立即触发第一轮（RunAtLoad 已保证，kickstart 兜底确保）
    platform_adapter._run_cmd(["launchctl", "kickstart", "-k", "system/com.ghlink.daemon"])
    print("[ghlink] 已启用值守并立即执行第一轮（LaunchDaemon，1 小时粒度）")
    return 0


def _macos_daemon_command() -> str:
    """macOS LaunchDaemon 执行入口：.app 内绝对路径 wrapper 优先，不依赖 /usr/local/bin 软链。

    v0.4.25（赛博根因 2026-08-26）：relocate 事故 → /usr/local/bin/ghlink 软链断 →
    _python_cmd() 回退裸 python -m → ModuleNotFoundError。
    .app 内 wrapper（/Applications/ghlink.app/Contents/MacOS/ghlink）自带 PYTHONPATH 与
    PATH 补全（v0.4.23），即使软链断了也能跑。
    """
    if sys.platform == "darwin":
        w = _find_wrapper()
        if w:
            return w
    # 非 macOS / 异常环境：走原 _python_cmd()（Windows frozen / dev 回退）
    return _python_cmd().strip(chr(34))


def _macos_pythonpath() -> str:
    """macOS 注入 PYTHONPATH：.app libexec + vendor（与 wrapper 一致，双保险）。"""
    if sys.platform == "darwin":
        app = _GHLINK_APP_LIBEXEC
        if os.path.isdir(app):
            return f"{app}:{app}/vendor"
    return ""


def _disable_macos() -> int:
    p = "/Library/LaunchDaemons/com.ghlink.plist"
    if os.path.exists(p):
        platform_adapter._run_cmd(["launchctl", "unload", p])
        # 2026-08-17 赛博提醒：unload 可能残留已加载实例，remove 按 Label 彻底清
        platform_adapter._run_cmd(["launchctl", "remove", "com.ghlink.daemon"])
        os.unlink(p)
    print("[ghlink] 已停用值守（LaunchDaemon 移除）")
    return 0


def _enable_windows() -> int:
    # P1 修复（赛博 2026-08-14）：参数数组传递，不用 split() 拆命令串——
    # /TR 的引号参数（含空格路径）必须作为单个元素，split() 会拆坏导致 schtasks 注册失败
    # 弹窗修复（李工 13:34 反馈）：值守用 windowed 入口（ghlink-watch.exe）静默跑，不弹命令行
    # v0.2.19（李工 8 条⑤）：schtasks 失败必须输出真实报错（原 _run_cmd 吞掉 stderr，
    # 导致「值守未运行」无法定位）；注册成功立即 /Run 触发第一轮
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        watch = os.path.join(exe_dir, "ghlink-watch.exe")
        tr = f'"{watch}" {_config_path()}'
    else:
        # 2026-08-17 Bug A 修复：非 frozen 也用 wrapper 入口（带 PYTHONPATH）
        tr = f"{_python_cmd()} {_config_path()}"
    args = [
        "schtasks",
        "/Create",
        "/TN",
        "ghlink",
        "/SC",
        "HOURLY",
        "/TR",
        tr,
        "/RL",
        "HIGHEST",
        "/RU",
        "SYSTEM",
        "/F",
    ]
    if not platform_adapter._run_cmd(args):
        # 输出真实报错（schtasks stderr），帮助定位「值守未运行」根因
        err = platform_adapter._run_cmd_output_error(args)
        print(
            f"[ghlink] enable 失败：schtasks /Create 未成功。原始输出：{err or '(无输出)'}",
            file=sys.stderr,
        )
        return 2
    # v0.2.19（李工 8 条②⑤）：注册成功立即触发第一轮 + 输出注册结果
    if platform_adapter._run_cmd(["schtasks", "/Run", "/TN", "ghlink"]):
        print("[ghlink] 已启用值守并立即执行第一轮（schtasks，1 小时粒度，最高权限）")
    else:
        print("[ghlink] 已启用值守（schtasks，1 小时粒度，最高权限）；首轮触发失败，将等整点")
    return 0


def _disable_windows() -> int:
    if not platform_adapter._run_cmd(["schtasks", "/Delete", "/TN", "ghlink", "/F"]):
        return 2
    print("[ghlink] 已停用值守（schtasks 移除）")
    return 0
