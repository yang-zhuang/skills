#!/bin/bash
# =============================================================================
# 目的：一次性摸清一台 AutoDL 实例的真实家底与外网连通性（只读，不修改任何东西）
#       - 系统盘 vs 数据盘剩余空间（决定模型能放哪、放多大）
#       - 容器真实配额（纠 free/nproc/lscpu 的宿主机虚值）
#       - GPU 显存
#       - Python 是否在 PATH（非交互 SSH 的常见坑）
#       - network_turbo 是否可用
#       - ModelScope 直连 / GitHub 走代理的实际速率
#
# 用法：本脚本【不在本机执行】。请把本文件正文作为 command，通过 shell MCP 的
#       execute_command 发到远端 AutoDL 实例执行。详见同目录 README.md。
#
# 约束：只读；自包含（不依赖在远端落盘、不 source 外部文件）；单条命令即可完成
# =============================================================================

set -u

echo "=============================================="
echo " AutoDL 实例体检  $(date)"
echo " 主机: $(hostname)"
echo "=============================================="

# ---------------------------------------------------------------------------
echo
echo "### 1. 磁盘（系统盘不可扩容 / 数据盘可扩容）"
# ---------------------------------------------------------------------------
df -h / /root/autodl-tmp 2>/dev/null

# ---------------------------------------------------------------------------
echo
echo "### 2. 容器真实配额（free / nproc / lscpu 显示的是宿主机虚值，不要用）"
# ---------------------------------------------------------------------------
echo "-- 内存配额 --"
if [ -r /sys/fs/cgroup/memory.max ]; then
  MEM=$(cat /sys/fs/cgroup/memory.max)
  GIB=$(awk -v b="$MEM" 'BEGIN{printf "%.1f", b/1024/1024/1024}')
  echo "cgroup memory.max = ${MEM} bytes  ≈ ${GIB} GiB"
else
  echo "未找到 /sys/fs/cgroup/memory.max（cgroup v1?）"
  cat /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null
fi

echo "-- CPU 配额 --"
if [ -r /sys/fs/cgroup/cpu.max ]; then
  RAW=$(cat /sys/fs/cgroup/cpu.max)
  CORES=$(awk -v s="$RAW" 'BEGIN{split(s,a,"/"); printf "%.1f", (a[2]>0? a[1]/a[2] : 0)}')
  echo "cgroup cpu.max = ${RAW}  ≈ ${CORES} 核"
else
  echo "未找到 /sys/fs/cgroup/cpu.max"
fi

echo "-- 对比：以下 free/nproc 通常是宿主机值，仅供参考 --"
free -g | head -2
echo "nproc = $(nproc)"

# ---------------------------------------------------------------------------
echo
echo "### 3. GPU"
# ---------------------------------------------------------------------------
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=index,name,memory.total,memory.used,memory.free,driver_version --format=csv
else
  echo "未安装 nvidia-smi（CPU 实例？）"
fi

# ---------------------------------------------------------------------------
echo
echo "### 4. Python 环境（非交互 SSH 下常不在 PATH）"
# ---------------------------------------------------------------------------
echo "当前 PATH 中的 python3: $(command -v python3 2>/dev/null || echo '未找到')"
python3 --version 2>/dev/null || echo "python3 不可用"
echo "-- conda/miniconda 是否存在（若上面未找到，需注入）--"
ls -d /root/miniconda3/bin/python3.* /opt/conda/bin/python3.* 2>/dev/null || echo "未发现常见 conda 路径"
echo "-- 校验 venv+ensurepip（很多安装脚本据此判断 Python 是否合格）--"
python3 -c 'import sys, venv, ensurepip; print("OK", sys.version_info[:3])' 2>/dev/null \
  || echo "当前 python3 不满足 venv+ensurepip，需先 export PATH=/root/miniconda3/bin:\$PATH"

# ---------------------------------------------------------------------------
echo
echo "### 5. network_turbo 是否可用"
# ---------------------------------------------------------------------------
if [ -r /etc/network_turbo ]; then
  echo "/etc/network_turbo 存在；source 后的代理地址："
  ( source /etc/network_turbo >/dev/null 2>&1; echo "  https_proxy=${https_proxy:-未设置}" )
else
  echo "未发现 /etc/network_turbo（非 AutoDL 镜像？）"
fi

# ---------------------------------------------------------------------------
echo
echo "### 6. 外网连通与速率"
echo "   基准：该类实例外网上限约 5 MB/s；明显低于此值时说明 turbo 可能未生效"
# ---------------------------------------------------------------------------
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY 2>/dev/null

echo "-- ModelScope 直连（国内源，不应走代理）--"
timeout 20 curl -sS -o /dev/null \
  -w '   code=%{http_code}  time=%{time_total}s\n' \
  https://www.modelscope.cn 2>&1

echo "-- GitHub codeload 走 turbo（取约 2MB 采样速率）--"
(
  source /etc/network_turbo >/dev/null 2>&1
  timeout 25 curl -sS -r 0-2097152 -o /dev/null \
    -w '   code=%{http_code}  speed=%{speed_download} bytes/s  size=%{size_download}  time=%{time_total}s\n' \
    https://codeload.github.com/cnchef/shell-mcp/tar.gz/refs/heads/main 2>&1
)

# ---------------------------------------------------------------------------
echo
echo "=============================================="
echo " 体检结论要点（请据以下几点判断）"
echo "=============================================="
DATAFREE=$(df -h /root/autodl-tmp 2>/dev/null | awk 'NR==2{print $4}')
echo "1) 数据盘可用空间：${DATAFREE}（模型必须放 /root/autodl-tmp/<项目>）"
echo "2) 内存以 cgroup memory.max 为准，不是 free 显示的值"
echo "3) 若 python3 未找到或不满足 venv+ensurepip："
echo "     export PATH=/root/miniconda3/bin:\$PATH"
echo "4) GitHub 仓库一律用 codeload tarball + curl，不要用 git clone（约 10KB/s 且易断）"
echo "5) 模型下载用 ModelScope 国内源，并配 no_proxy=www.modelscope.cn 与代理分流"
echo "6) 服务要外网访问必须监听 127.0.0.1:6006 或 6008"
echo "=============================================="
