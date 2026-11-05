# AutoDL 平台约定与配额排查

## 目的

澄清 AutoDL（含恒源云 / SeetaCloud 入口）实例的**磁盘布局、真实配额读取方式、网络加速用法**，
避免把宿主机看到的虚值当成可用资源，也避免把大模型放到不可扩容的系统盘上。

---

## 磁盘布局（最重要的一条约定）

| 位置 | 容量 | 可否扩容 | 放什么 |
|---|---|---|---|
| `/`（overlay） | 通常仅 **30G** | **用户不可扩容** | 代码：`/root/code/<项目>` |
| `/root/autodl-tmp` | 可在控制台扩容 | ✅ 可扩容 | **模型权重、数据集、日志等大件**：`/root/autodl-tmp/<项目>` |

平台控制台通常这样呈现两个盘：

```
系统盘/                      30G
数据盘 /root/autodl-tmp      150G（以实际扩容结果为准）
```

**推论**：任何安装器若把模型默认放在仓库旁（如 `<仓库>/data`），都必须显式指定到 `/root/autodl-tmp` 下的路径，
否则会写爆 30G 系统盘。

检查方式：

```bash
df -h /root/autodl-tmp
df -h /root
```

---

## 配额排查：容器真实值 vs 宿主机虚值

**经典坑**：容器内执行 `free`、`nproc`、`lscpu` 得到的是**宿主机**信息，不是分配给该容器的配额。
若据此判断可用内存/CPU，会把资源高估数倍（例如宿主机 503GB 内存 vs 容器配额 62GB）。

**正确做法：读 cgroup 配额文件**

```bash
cat /sys/fs/cgroup/memory.max        # 内存配额（字节）
cat /sys/fs/cgroup/cpu.max           # CPU 配额，形如 1200000/100000 → 12 核
```

换算示例：`memory.max = 96636764160` → `96636764160 / 1024^3 = 90 GiB`。

显存则通过 GPU 工具读取，通常是真实值：

```bash
nvidia-smi
nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv
```

一次性摸清家底可直接用 `scripts/autodl-instance-probe.sh`。

---

## CPU 亲和性：cpuset 与 cpu.max 是两回事

多租容器常见的组合是：**可用的 CPU 编号放开一大片，但 CPU 时间只给几个核的份额**。实测（双路 Xeon 8260 实例）：

```bash
cat /sys/fs/cgroup/cpuset.cpus.effective   # 0-95   → 允许用到全部 96 个逻辑核
cat /sys/fs/cgroup/cpuset.mems.effective   # 0-1    → 允许用到两个 NUMA 节点
cat /sys/fs/cgroup/cpu.max                 # 1200000/100000 → 但总共只有 12 核的时间片
```

**后果与误区**：

- `nproc` 会显示 96（宿主机值），据此推断"能用 96 核"是错的，真实算力上限是 12 核。
- 线程可以在 96 个核上随意迁徙，**跨 NUMA 节点的迁移本身是可预期的**（内存分布与线程分布常常错位）。
- 但**不要据此就去 `taskset` 绑核**：在"时间片只有 12 核、可用编号却有 96 个"的场景下，
  把几十个线程挤进单个 socket 反而会引起超线程兄弟核争用。实测绑 node0 与不绑的差异仅约 2 tok/s，
  落在噪声内，且绑核略慢。**默认不动它。**
- **凡是按"宿主机核数"自动铺线程池的软件，在配额容器里都会严重过载，必须显式改小到与 `cpu.max` 匹配。**
  实测：某推理引擎默认按宿主机铺 47 个 CPU worker，在 12 核配额下混合负载只有 ~31 tok/s
  （GPU 利用率掉到 3–35%，专家供给 starving）；把 worker 数改成 11（= 12 核配额 − host loop 占 1 核）后
  **涨到 112–119 tok/s，约 3.7 倍**。这类参数通常叫 `--pool-workers` / `--threads` / `-j`，
  取值经验：**cpu.max 的核数 − 1**。

若确实要做绑核实验，注意 `taskset -cp <mask> <PID>` **只改主线程**，子线程要逐个设置：

```bash
for t in $(ls /proc/<PID>/task); do taskset -cp 0-23,48-71 $t >/dev/null 2>&1; done
```

查看线程当前落在哪个核（`/proc/<PID>/task/<TID>/stat` 第 39 字段是 `psr`）：

```bash
for t in $(ls /proc/<PID>/task); do awk '{print $39}' /proc/<PID>/task/$t/stat; done | sort -n | uniq -c
```

判断某进程的内存落在哪个 NUMA 节点：

```bash
grep -oE 'N[01]=[0-9]+' /proc/<PID>/numa_maps | awk -F'=' '{s[$1]+=$2} END {for(k in s) print k, s[k]*4096/1073741824" GB"}'
```

---

## 测推理速度前必须预热（否则会低估 40% 以上）

带 MoE 专家缓存 / 投机解码的推理服务（如 Strata），**首轮请求的速度远低于稳态**。实测 IQ3_S 档：

| 阶段 | decode expert cache 命中率 | 实测 tok/s |
|---|---|---|
| 刚启动的头几次请求 | 98.1% | 25 – 33 |
| 跑过十几轮之后 | 99.5% | 40 – 47 |
| 完全预热 | **99.9%**（经 PCIe 取的专家 0.0%） | **49 – 65（均值 58）** |

**因此：任何一次性的冷启动读数都不能当作性能指标。** 正确的做法是
**连续跑 3–5 次相同请求，取后几次**，并同时看服务日志里的缓存命中率是否已经爬到平台期：

```bash
strings <服务日志> | grep -aiE 'cache hit|prefetch|routed experts' | tail -8
```

对照实验还要注意**顺序混淆**：如果先测 A 再测 B，两者的差异可能只是"越跑越热"，
必须做交叉对照（A→B→A→B）才能把预热漂移与真实效应分开。

### 更大的坑：测速必须轮换不同数据，不能同一条 prompt 复读

用**同一条 prompt 连发**测速，速度会被"背题"抬高近一倍。实测 Strata IQ3_S 同机对照：

| 负载 | 专家缓存命中率 | GPU 利用率 | tok/s |
|---|---|---|---|
| 同一条 prompt 复读 | 99.9%（经 PCIe 的专家 0.0%） | 钉在 ~100% | 均值 58 |
| 12 条不同 prompt 轮换 | 99.4–99.7% | 在 3–100% 间大幅波动 | **均值 31–33** |

机制：同一请求反复路由到同一批专家，VRAM 缓存热集与这份负载完美对齐；
真实混合负载下每次请求要拉不同专家，缓存未命中的同步取数让 GPU 出现大量空转。

因此：

- "跑 3–5 次取后几次"只有在**每次请求内容都不同**时才给出真实吞吐；同 prompt 复读的数字只是上界。
- **判断瓶颈也必须在混合负载下做**：同 prompt 下 GPU 100% 会误判成"GPU 瓶颈、调 CPU 无用"——
  同一台机器换混合负载后 GPU 就掉到 3–35%，瓶颈其实在缓存未命中 / PCIe / CPU 一侧。
- 换回同一条 prompt 速度会立刻开始回升（实测 36 → 52），可用来验证"背题效应"。

---

## 环境注入：非交互 SSH 下 Python 不在 PATH

远程实例的非交互 shell 里，`python3` / `pip` **不在 PATH** 中，真实位置通常是 conda/miniconda 下：

```
/root/miniconda3/bin/python3.12
```

不注入 PATH 会导致什么：安装脚本（如 Strata 的 `setup.sh`）找不到满足 `Python 3.10+ with venv` 的解释器，
于是尝试 `apt-get install`，而 AutoDL 容器虽然是 root 但**没有 `sudo` 二进制**，最终报：

```
Python 3.10+ with venv is needed; installing it (sudo will ask for your password) ...
setup.sh: 35: sudo: not found
```

**每条需要 Python 的命令都要先注入**：

```bash
export PATH=/root/miniconda3/bin:$PATH
python3 --version
```

---

## 网络加速：network_turbo

```bash
source /etc/network_turbo
```

要点：

- **只对当前 shell / 当前命令生效**。新开的执行、下一次调用都要重新 source。
- 主要用途：**pip 安装依赖**、访问 GitHub / HuggingFace 等境外源。
- **ModelScope 等国内源不需要 turbo**（绕代理反而可能更慢）。
- source 之后会设置 `http_proxy` / `https_proxy`（形如 `http://<内网IP>:<端口>`）。

判断 turbo 是否真的在加速：**看下载速度**。很慢或返回 503 就是没生效/代理异常。

---

## 外网带宽基准

实测该类实例的**外网下载速度约 2–5 MB/s（约 16–40 Mbps），且会随下载时长回落**。有以下三点支撑：

- 走 turbo 代理拉取 codeload 包：约 2.5–3.9 MB/s
- 直连 ModelScope（阿里云 CDN）峰值：约 5 MB/s
- **长时间下载会衰减**：83.6 GB 模型全程实测 5.4 → 2.25 → 2.64 MB/s 波动，并非单调，也达不到峰值

三者同量级，说明**换源基本改善不了太多**。**长时间大文件要给余量**，按 3 MB/s 保守估算：

| 文件大小 | 乐观（5 MB/s） | 保守（3 MB/s） |
|---|---|---|
| 20 GB | ~1.1 小时 | ~1.9 小时 |
| 84 GB | ~4.7 小时 | ~8 小时 |

> 例外：git 的 pack 协议在 turbo 代理下会被额外限速到约 10 KB/s，属特例，见 `autodl-download.md`。

---

## 常见软件缺失

容器镜像通常**不含**：

- `sudo`（是 root 但没有该二进制）→ 依赖 `sudo` 的安装脚本会失败
- 较新版本的系统工具（如系统 cmake 版本偏旧）→ 见 `autodl-download.md` 的 toolchain 章节

应对思路：优先使用 **Python venv 提供的新版本工具**，并注意 **PATH 顺序**（venv 的 bin 要放在系统路径之前）。
