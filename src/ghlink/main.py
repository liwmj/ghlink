"""入口：单轮执行（调度粒度 1min，由平台定时任务调用）。

流程：锁 → 探测 → 计数判定（成功清零）→ 触发则 取IP→备份→写入→flushdns→自检
→ 成功更新状态 / 失败回滚+degraded → 告警（冷却期去重）→ 写状态文件。

退出码：0=正常（含跳过） 1=降级/告警 2=配置/参数错误（供定时任务日志区分）
"""
import sys
from typing import Any, Dict


def run(config_path: str = "config.json") -> int:
    """单轮执行主流程，返回退出码。"""
    # TODO(顾笙): 按 README 流程图实现；平台差异只许走 platform_adapter
    return 0


def main() -> None:
    try:
        sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else "config.json"))
    except Exception as exc:  # 兜底：任何异常不裸崩，记日志退出 1
        print(f"[ghlink] fatal: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
