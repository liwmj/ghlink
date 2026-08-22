"""hosts 段落式管理：写入/备份/回滚/自检。

约定（借鉴 GitHub520 思路，全新实现）：
- hosts 段落标记：# ghlink Start / # ghlink End，段落可重复安全更新
- GitHub520 静态兜底子段：# ghlink520 Start / # ghlink520 End（v0.2.19 起）
  —— 初始化时合入一次，后续动态更新自动保留（不重复合入、不丢失）
- 写入前 backup_hosts()，写入后立即自检（probe 替换域名），失败 restore_hosts()
- 自检失败 → 回滚 + degraded 状态 + 告警，坏配置绝不留场
- v0.2.19（李工 8 条）：正常态也保持 hosts 段存在（全局访问生效），
  写入前与现有段落比较，内容无变化不落盘（避免频繁写盘/flushdns）
"""

from typing import Dict, List

from . import platform_adapter

START_MARK = "# ghlink Start"
END_MARK = "# ghlink End"
G520_START = "# ghlink520 Start"
G520_END = "# ghlink520 End"


def build_block(entries: Dict[str, List[str]]) -> str:
    """由 {domain: [ips]} 生成 hosts 段落文本（含 Start/End 标记）。"""
    lines = [START_MARK]
    for domain, ips in entries.items():
        for ip in ips:
            lines.append(f"{ip} {domain}")
    lines.append(END_MARK)
    return "\n".join(lines) + "\n"


def build_combined_block(dynamic: Dict[str, List[str]], g520: Dict[str, List[str]]) -> str:
    """生成「动态段 + GitHub520 静态子段」复合段落。

    结构：
        # ghlink Start
        <动态 IP：8 域名>
        # ghlink520 Start
        <GitHub520 社区 IP：非核心域名>
        # ghlink520 End
        # ghlink End

    g520 为空时不输出子段标记，段落保持原样（向后兼容旧 hosts）。
    """
    lines = [START_MARK]
    for domain, ips in dynamic.items():
        for ip in ips:
            lines.append(f"{ip} {domain}")
    if g520:
        lines.append(G520_START)
        for domain, ips in g520.items():
            for ip in ips:
                lines.append(f"{ip} {domain}")
        lines.append(G520_END)
    lines.append(END_MARK)
    return "\n".join(lines) + "\n"


def _read_hosts(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _write_hosts(path: str, content: str) -> bool:
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except OSError:
        return False


def _extract_section(content: str, start_mark: str, end_mark: str) -> str:
    """提取段落内部文本（不含标记）；段落缺失/标记不完整返回 ''。"""
    start = content.find(start_mark)
    end = content.find(end_mark)
    if start == -1 or end == -1 or end <= start:
        return ""
    return content[start + len(start_mark) : end]


def current_ghlink_block(path: str = "") -> str:
    """读取当前 hosts 中的 # ghlink Start/End 段全文（含标记）；不存在返回 ''。"""
    path = path or platform_adapter.get_hosts_path()
    content = _read_hosts(path)
    start = content.find(START_MARK)
    end = content.find(END_MARK)
    if start == -1 or end == -1 or end <= start:
        return ""
    return content[start : end + len(END_MARK)]


def current_g520_entries(path: str = "") -> Dict[str, List[str]]:
    """从当前 hosts 提取 GitHub520 子段条目 {domain: [ips]}；无子段返回 {}。

    v0.2.19：初始化合入后，动态更新时从现有 hosts 保留该子段（不重复拉取/合入）。
    """
    path = path or platform_adapter.get_hosts_path()
    content = _read_hosts(path)
    section = _extract_section(content, G520_START, G520_END)
    if not section:
        return {}
    entries: Dict[str, List[str]] = {}
    for line in section.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        ip, domain = parts[0], parts[1].rstrip(".")
        entries.setdefault(domain, [])
        if ip not in entries[domain]:
            entries[domain].append(ip)
    return entries


def apply_block(
    block: str,
    backup_dir: str = "backup",
    preserve_g520: bool = True,
) -> tuple:
    """写入 hosts（替换旧段落）；提权/写入失败返回 (False, "")。

    参数：
    - block: 新段落全文（含 # ghlink Start/End 标记）
    - preserve_g520: 若现有 hosts 含 GitHub520 子段而新 block 不含，
      则自动保留子段（v0.2.19 初始化合一次、动态更新不丢兜底段）

    返回 (ok, backup_path)：ok=False 表示写入失败；ok=True 时 backup_path
    为本次写入前的备份文件路径（供自检失败回滚使用）。
    """
    if not platform_adapter.ensure_privilege():
        return False, ""
    path = platform_adapter.get_hosts_path()
    content = _read_hosts(path)

    # v0.2.19：动态段更新时保留现有 GitHub520 子段（初始化已合入，不重复拉取）
    if preserve_g520 and G520_START not in block:
        g520 = _extract_section(content, G520_START, G520_END)
        if g520:
            # 把子段插入 # ghlink End 之前
            end_pos = block.rfind(END_MARK)
            if end_pos != -1:
                sub = f"\n{G520_START}{g520}{G520_END}"
                block = block[:end_pos] + sub + block[end_pos:]

    # v0.4.0（李工 12:35 点 3）：段落插到文件最前优先命中（first-match-wins），
    # 避免用户预存条目在段落前遮蔽 ghlink 写入；段落外内容零改动
    start = content.find(START_MARK)
    end = content.find(END_MARK)
    if start != -1 and end != -1 and end > start:
        before = content[:start]
        after = content[end + len(END_MARK) :]
        content = before + block + after
        # 若段落不在文件最前（前面还有非空内容），把段落提前到最前
        if before.strip():
            content = block + "\n" + before + after
    elif start == -1 and end == -1:
        content = block + "\n" + content.rstrip("\n") + "\n"
    else:
        # 段落标记不完整，视为异常：整体重建安全内容
        return False, ""

    # v0.2.19：内容无变化不落盘（避免每轮写盘 + flushdns）
    if content == _read_hosts(path):
        return True, ""

    backup = platform_adapter.backup_hosts(backup_dir)
    if not backup:
        return False, ""
    if not _write_hosts(path, content):
        platform_adapter.restore_hosts(backup)
        return False, ""
    platform_adapter.flush_dns()
    return True, backup


def remove_block(path: str = "") -> bool:
    """v0.4.1（拂晓 Linux 严格测试发现）：移除 hosts 中的 ghlink 段落（含 ghlink520 子段），
    还原基线（disable/卸载时调用，李工"卸载也直接删"要求）。段落不存在返回 True（幂等）。"""
    if not platform_adapter.ensure_privilege():
        return False
    path = path or platform_adapter.get_hosts_path()
    content = _read_hosts(path)
    start = content.find(START_MARK)
    end = content.find(END_MARK)
    if start == -1 or end == -1 or end <= start:
        return True  # 无段落，幂等成功
    # 移除段落（含段落前后的多余空行清理）
    before = content[:start]
    after = content[end + len(END_MARK) :]
    new_content = before + after
    # 清理段落移除后残留的双空行
    while "\n\n\n" in new_content:
        new_content = new_content.replace("\n\n\n", "\n\n")
    if new_content == content:
        return True
    if not _write_hosts(path, new_content):
        return False
    platform_adapter.flush_dns()
    return True


def detect_external_dupes(path: str = "") -> Dict[str, str]:
    """v0.4.0（李工 12:35 点 3）：检测段落外预存的 GitHub 生态域名条目。

    返回 {domain: "ip"}——用户在 ghlink 块之外已配置的条目，
    first-match-wins 下可能与 ghlink 写入冲突。enable 时调用，命中则告警+备份。
    """
    path = path or platform_adapter.get_hosts_path()
    content = _read_hosts(path)
    # 剔除 ghlink 段落（含 ghlink520 子段）
    start = content.find(START_MARK)
    end = content.find(END_MARK)
    if start != -1 and end != -1 and end > start:
        content = content[:start] + content[end + len(END_MARK) :]
    out: Dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        ip, domain = parts[0], parts[1].rstrip(".")
        if domain.endswith(
            (".github.com", "github.com", "githubusercontent.com", "githubassets.com", "fastly.net")
        ):
            out.setdefault(domain, ip)
    return out


def verify_after_apply(targets: List[str], timeout_sec: float) -> bool:
    """写入后立即自检：新 IP 下全部目标连通才算成功。

    v0.4.4（李工 03:27 终裁 B 方案，顾笙无缓存场景专项发现）：分级宽容降级——
    先三层全检（TCP+TLS+HTTP HEAD），失败目标用 TCP-only 复检：
    TCP 通判通过（与预检同口径，防 TLS 干扰误杀——TLS 握手被干扰但 IP 实际可达
    时不再回滚清空兜底写入）；TCP 也不通才判失败（真坏 IP 仍回滚，坏配置绝不留场）。
    """
    from . import probe

    results = probe.probe_all(targets, timeout_sec)
    failed = [h for h, r in results.items() if not r.get("ok")]
    if not failed:
        return True
    # 分级宽容：失败目标 TCP-only 复检（TCP 通即通过）
    tcp_results = probe.probe_tcp_only_many(failed, timeout_sec)
    return all(r.get("ok") for r in tcp_results.values())


def rollback(backup_path: str) -> bool:
    """回滚 hosts 到备份版本。"""
    return platform_adapter.restore_hosts(backup_path)
