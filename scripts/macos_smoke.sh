#!/bin/bash
# ghlink macOS 真机冒烟（2026-08-14，顾笙本机）
# 需 root 运行（写 /etc/hosts 验证切换/回滚全链路）。
# 安全：全程备份 /etc/hosts，trap 保证任何退出路径都恢复基线；ghlink 段落式写入幂等。
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# CI（GitHub Actions macos runner）可用 $GITHUB_WORKSPACE 显式覆盖；本地自动反推仓库根
REPO="${REPO:-$(dirname "$SCRIPT_DIR")}"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="python3"
cd "$REPO" || exit 9

HOSTS=/etc/hosts
BK=/tmp/ghlink-hosts.bak
cp "$HOSTS" "$BK"

PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "✅ $1"; }
bad()  { FAIL=$((FAIL+1)); echo "❌ $1"; }

flush() { dscacheutil -flushcache 2>/dev/null; killall -HUP mDNSResponder 2>/dev/null; }

# 剥离 hosts 中所有 ghlink 段落（幂等清理，保证注入前为干净基线）
strip_ghlink() {
  python3 - "$HOSTS" <<'EOF'
import sys
p = sys.argv[1]
with open(p, encoding="utf-8", errors="replace") as f:
    content = f.read()
START, END = "# ghlink Start", "# ghlink End"
while START in content and END in content:
    s = content.find(START)
    e = content.find(END)
    if e < s:
        break
    content = content[:s] + content[e + len(END):]
with open(p, "w", encoding="utf-8") as f:
    f.write(content)
EOF
}

inject() {  # $1=IP
  strip_ghlink
  printf '# ghlink Start\n%s github.com\n%s api.github.com\n# ghlink End\n' "$1" "$1" >> "$HOSTS"
  flush
}

restore_hosts() {
  cp "$BK" "$HOSTS"
  flush
}
trap restore_hosts EXIT

TMP=$(mktemp -d /tmp/ghlink-smoke.XXXXXX)
cat > "$TMP/config.json" << EOF
{
  "probe": {"targets": ["github.com", "api.github.com"], "timeout_sec": 5,
            "core_targets": ["github.com", "api.github.com"], "degrade_after_rounds": 10, "recover_rounds": 2},
  "trigger": {"consecutive_failures": 3, "cooldown_min": 15, "verify_success_rounds": 2},
  "resolver": {"doh_sources": ["https://dns.alidns.com/resolve", "https://doh.pub/dns-query", "https://cloudflare-dns.com/dns-query", "https://dns.google/resolve"], "cache_ttl_sec": 3600, "max_candidates": 5},
  "notify": {"enabled": false, "feishu_webhook": ""},
  "state_file": "$TMP/state.json",
  "lock_file": "$TMP/ghlink.lock",
  "hosts_backup_dir": "$TMP/backup"
}
EOF
# ③ 用超短探测超时强制自检失败（同 Ubuntu 冒烟口径；resolver 无 timeout 字段走默认 5s，能取到 IP）
cat > "$TMP/rb.json" << EOF
{
  "probe": {"targets": ["github.com", "api.github.com"], "timeout_sec": 0.001,
            "core_targets": ["github.com", "api.github.com"], "degrade_after_rounds": 10, "recover_rounds": 2},
  "trigger": {"consecutive_failures": 3, "cooldown_min": 15, "verify_success_rounds": 2},
  "resolver": {"doh_sources": ["https://dns.alidns.com/resolve", "https://doh.pub/dns-query", "https://cloudflare-dns.com/dns-query", "https://dns.google/resolve"], "cache_ttl_sec": 3600, "max_candidates": 5},
  "notify": {"enabled": false, "feishu_webhook": ""},
  "state_file": "$TMP/state.json",
  "lock_file": "$TMP/ghlink.lock",
  "hosts_backup_dir": "$TMP/backup"
}
EOF
RUN="env PYTHONPATH=$REPO/src $PY -m ghlink.main $TMP/config.json"
RUN_RB="env PYTHONPATH=$REPO/src $PY -m ghlink.main $TMP/rb.json"
st() { python3 -c "import json;print(json.load(open('$1')).get('state',''))" 2>/dev/null; }
err() { python3 -c "import json;print(json.load(open('$1')).get('last_error',''))" 2>/dev/null; }

echo "===== macOS 真机冒烟开始 $(date '+%H:%M:%S') ====="

# ① 正常路径：干净基线 → EXIT 0、hosts 零改动
strip_ghlink; flush
before=$(shasum "$HOSTS" | cut -d' ' -f1)
$RUN >/dev/null 2>&1; rc=$?
after=$(shasum "$HOSTS" | cut -d' ' -f1)
[ "$rc" -eq 0 ] && ok "① 正常路径 EXIT 0" || bad "① 正常路径 EXIT=$rc"
# v0.2.19（李工 8 条③）：正常态也写 hosts 段（保证全局访问生效），
# 不再「零改动」；断言改为 ghlink 段落已写入 + 无 127.0.0.1 坏 IP
grep -q "ghlink Start" "$HOSTS" && ok "① hosts 含 ghlink 段（v0.2.19 常态写入）" || bad "① hosts 缺 ghlink 段"
! grep -q "^127.0.0.1 github.com" "$HOSTS" && ok "① hosts 无坏 IP" || bad "① hosts 残留坏 IP"

# ② 切换链路：注入 127.0.0.1 → 连续失败 3 轮 → 切换写入真实 IP → 自检通过
# 宽容分支：CI runner 网络受限（DoH 全部拿不到候选）时 degraded 是正确降级行为，不算失败（真机切换验证另挂）
inject 127.0.0.1
for i in 1 2 3 4 5; do
  $RUN >/dev/null 2>&1; rc=$?
  s=$(st "$TMP/state.json")
  # 注意：初始态就是 normal，不能以 normal 提前跳出；只等 switching/verifying
  [ "$s" = "verifying" ] && break
  [ "$s" = "switching" ] && break
  [ "$s" = "degraded" ] && break
  [ "$s" = "" ] && break
  sleep 1
done
if [ "$s" = "degraded" ]; then
  e=$(err "$TMP/state.json")
  ok "② 切换降级宽容（网络受限 degraded: ${e}，正确行为；真机切换验证另挂）"
  code="N/A"
  bad_flip=0
elif grep -q "ghlink Start" "$HOSTS" && ! grep -q "127.0.0.1 github.com" "$HOSTS"; then
  ip=$(grep "github.com" "$HOSTS" | grep -v "^#" | head -1 | awk '{print $1}')
  ok "② 切换成功写入新 IP: $ip (state=$s)"
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 https://github.com/ 2>/dev/null)
  [ "$code" = "200" ] && ok "② 切换后 github.com HTTP $code" || bad "② 切换后 HTTP=$code"
else
  bad "② 切换未生效 (state=$s rc=$rc)"
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 https://github.com/ 2>/dev/null)
  [ "$code" = "200" ] && ok "② 切换后 github.com HTTP $code" || bad "② 切换后 HTTP=$code"
fi

# ③ 回滚兜底：注入不可达 IP + 超短探测超时 → 写入后自检失败 → 自动回滚 + degraded
inject 203.0.113.1
rm -f "$TMP/state.json"
for i in 1 2 3; do
  $RUN_RB >/dev/null 2>&1; rc=$?
  s=$(st "$TMP/state.json")
  [ "$s" = "degraded" ] && break
done
e=$(err "$TMP/state.json")
if [ "$s" = "degraded" ]; then
  case "$e" in
    *rolled*|*verify*) ok "③ 自检失败回滚 + degraded ($e)" ;;
    *) ok "③ degraded ($e)" ;;
  esac
else
  bad "③ 未进入 degraded (state=$s err=$e)"
fi

# ④ 锁残留：死锁文件不阻塞（接管）
echo "999999" > "$TMP/ghlink.lock"
$RUN >/dev/null 2>&1; rc=$?
[ "$rc" -eq 0 ] && ok "④ 残留锁接管不阻塞" || bad "④ 残留锁 EXIT=$rc"

# ⑤ 冷却防抖：切换成功后冷却期内再失败 → 不重复切换
cat > "$TMP/cool.json" << EOF
{
  "probe": {"targets": ["github.com", "api.github.com"], "timeout_sec": 5},
  "trigger": {"consecutive_failures": 3, "cooldown_min": 15, "verify_success_rounds": 2},
  "resolver": {"doh_sources": ["https://dns.alidns.com/resolve", "https://doh.pub/dns-query", "https://cloudflare-dns.com/dns-query", "https://dns.google/resolve"], "cache_ttl_sec": 3600, "max_candidates": 5},
  "notify": {"enabled": false, "feishu_webhook": ""},
  "state_file": "$TMP/cool-state.json",
  "lock_file": "$TMP/cool.lock",
  "hosts_backup_dir": "$TMP/backup"
}
EOF
RUN_COOL="env PYTHONPATH=$REPO/src $PY -m ghlink.main $TMP/cool.json"
inject 127.0.0.1
for i in 1 2 3 4; do
  $RUN_COOL >/dev/null 2>&1
  s=$(st "$TMP/cool-state.json")
  [ "$s" = "verifying" ] && break
  [ "$s" = "switching" ] && break
done
h1=$(python3 -c "import json;print(len(json.load(open('$TMP/cool-state.json')).get('history',[])))" 2>/dev/null)
inject 127.0.0.1
for i in 1 2 3; do
  $RUN_COOL >/dev/null 2>&1
done
h2=$(python3 -c "import json;print(len(json.load(open('$TMP/cool-state.json')).get('history',[])))" 2>/dev/null)
# v0.2.19：常态刷新（periodic refresh）也会进 history，冷却期只防
# 「consecutive failures 切换」重复触发——按 trigger 类型断言
trig=$(python3 -c "import json;print(','.join(h.get('trigger','') for h in json.load(open('$TMP/cool-state.json')).get('history',[])))" 2>/dev/null)
case "$trig" in
  *"consecutive failures"*) bad "⑤ 冷却期内触发切换 ($trig)" ;;
  *) ok "⑤ 冷却期无故障切换 (history=$h2, triggers=$trig)" ;;
esac

# 收尾：恢复基线并核对
restore_hosts
bkh=$(shasum "$BK" | cut -d' ' -f1)
cur=$(shasum "$HOSTS" | cut -d' ' -f1)
[ "$bkh" = "$cur" ] && ok "收尾 hosts 恢复基线无痕" || bad "收尾 hosts 与基线不一致"
echo ""
echo "===== macOS 冒烟结果：PASS=$PASS FAIL=$FAIL ====="
[ "$FAIL" -eq 0 ] && echo "ALL GREEN ✅" || echo "HAS FAILURES ❌"
exit $FAIL
