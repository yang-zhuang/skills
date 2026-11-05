#!/bin/bash
# =============================================================================
# 目的：检查 AutoDL 实例的端口暴露情况（只读）
#       AutoDL 只对外映射 6006 / 6008 两个端口，任何监听在其它端口的服务外网一律访问不到。
#       本脚本回答两件事：
#         1) 6006 / 6008 上有没有服务在监听？服务本身是否健康（本机 curl 通过）？
#         2) 有没有服务正跑在【不会被映射】的端口上（8080 / 7860 / 8000 / 11434 等），需要改端口？
#
# 用法：本脚本【不在本机执行】。请把本文件正文作为 command，通过 shell MCP 的
#       execute_command 发到远端 AutoDL 实例执行。详见同目录 README.md。
#
# 约束：只读；自包含（不依赖在远端落盘、不 source 外部文件）；单条命令即可完成
# =============================================================================

set -u

# ---------------------------------------------------------------------------
# 端口是否处于 LISTEN
#   注意：很多 AutoDL 容器里 ss 和 netstat【都不存在】（实测 ss: command not found、
#   netstat: n/a）。只依赖它们会得出「没有服务在监听」的【假阴性】结论——
#   实测就出现过：curl 明明通，ss 却说没监听。
#   因此最终一律回退到 /proc/net/tcp，它无需任何工具，最可靠。
#   /proc/net/tcp 字段：$2=本地地址(0100007F:1776)  $4=状态(0A=LISTEN)
# ---------------------------------------------------------------------------
port_listening() {
  P=$1
  HX=$(printf '%04X' "$P")
  if command -v ss >/dev/null 2>&1; then
    ss -tln 2>/dev/null | grep -qE ":${P} " && return 0
  fi
  if command -v netstat >/dev/null 2>&1; then
    netstat -tln 2>/dev/null | grep -qE ":${P} " && return 0
  fi
  awk -v hx="$HX" 'NR>1 { split($2,a,":"); if (toupper(a[2])==hx && $4=="0A") found=1 } END{ exit !found }' \
    /proc/net/tcp 2>/dev/null && return 0
  awk -v hx="$HX" 'NR>1 { split($2,a,":"); if (toupper(a[2])==hx && $4=="0A") found=1 } END{ exit !found }' \
    /proc/net/tcp6 2>/dev/null && return 0
  return 1
}

echo "=============================================="
echo " AutoDL port check  $(date)"
echo "=============================================="

# ---------------------------------------------------------------------------
echo
echo "### 1. Listening TCP ports"
# ---------------------------------------------------------------------------
if command -v ss >/dev/null 2>&1; then
  ss -tlnp 2>/dev/null
elif command -v netstat >/dev/null 2>&1; then
  netstat -tlnp 2>/dev/null
else
  echo "(ss / netstat not available - reading /proc/net/tcp directly)"
  awk 'NR>1 && $4=="0A" { print "  LISTEN  " $2 }' /proc/net/tcp 2>/dev/null
  awk 'NR>1 && $4=="0A" { print "  LISTEN  " $2 }' /proc/net/tcp6 2>/dev/null
fi

# ---------------------------------------------------------------------------
echo
echo "### 2. 6006 / 6008 (the only ports mapped to the public internet)"
# ---------------------------------------------------------------------------
for P in 6006 6008; do
  echo "--- port $P ---"
  if port_listening "$P"; then
    echo "  status: LISTENING"
    CODE=$(curl -sS -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:$P" 2>/dev/null)
    HEALTH=$(curl -sS -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:$P/health" 2>/dev/null)
    echo "  local self-check: root HTTP=${CODE:-no-response}  /health HTTP=${HEALTH:-no-such-endpoint}"
  else
    echo "  status: free / nothing listening (external access WILL fail)"
  fi
done

# ---------------------------------------------------------------------------
echo
echo "### 3. WARNING: services on ports that are NOT mapped"
echo "    (process runs fine and works locally, but is unreachable from outside)"
# ---------------------------------------------------------------------------
PORTLIST="8080 7860 8000 11434 8888 5000 3000 8501"
FOUND=0
for P in $PORTLIST; do
  if port_listening "$P"; then
    FOUND=1
    echo "  !! port $P is listening - unreachable from outside, switch it to 6006 or 6008"
  fi
done
[ "$FOUND" -eq 0 ] && echo "  none"

# ---------------------------------------------------------------------------
echo
echo "=============================================="
echo " What to do next"
echo "=============================================="
cat <<'NOTE'
1) A service must listen on 127.0.0.1:6006 or 127.0.0.1:6008 to be mapped to
   https://<random-string>.seetacloud.com:8443
2) Only 2 ports available - decide which service takes 6006 and which takes 6008
3) How to change the port:
   Strata            : ./setup.sh --port 6006   (its default is 8080!)
   Gradio            : --server-port 6006
   FastAPI / Uvicorn : --port 6006
   vLLM              : --port 6006
   Ollama            : 6006
4) Protocol defaults to http mapping; switching to https requires POWERING OFF
   first, then changing it in the instance spec details
5) Troubleshooting order: is it LISTENING -> does local curl work -> then test
   the public URL

GOTCHAS learned the hard way:
- ss and netstat are often ABSENT in these containers. Never conclude "not
  listening" from their empty output alone; fall back to /proc/net/tcp.
- Restarting a big model server: after kill, the model unload takes a while and
  the port stays bound. Starting the new one too early fails with
  "port 6006 is already in use". Verify the port is free before restarting.
- Accessing via the public hostname often trips the service's DNS-rebinding
  protection (HTTP 403 "Host ... is not allowed"). Fix by adding the public
  hostname to allowed_hosts in the config, or set an api_key.
NOTE
echo "=============================================="
