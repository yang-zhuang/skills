# 07 · 平台：Linux / WSL2 / Windows 原生，该在哪跑

## 一句话结论

**能用 Linux 就用 Linux；在 Windows 上，能进 WSL2 就进 WSL2；Windows 原生是最后选择。**

原因很直接：训练生态（flash-attn、deepspeed、bitsandbytes 的某些 kernel、vllm）主要在 Linux 上
开发和测试，Windows 原生经常是"能装但跑不起来"，或者性能差一截。WSL2 里基本等同 Linux。

## 先探测，别猜

```bash
python scripts/probe_env.py
```

报告里 `## 平台` 那段会告诉你：

- 当前是 Windows / Linux / WSL（WSL 会显示发行版名）
- Windows 上有没有装 WSL、装了哪几个发行版
- 技能包是不是落在 `/mnt/`（Windows 盘）下
- 一句**建议**（明确标了"不是事实"）

## 三种环境的取舍

| | Linux 原生 | WSL2 | Windows 原生 |
|---|---|---|---|
| 训练库支持 | 最全 | 基本等同 Linux | 缺 flash-attn / deepspeed / vllm 等 |
| 显存 | 直连 | 由 Windows 驱动透传，可用 | 直连 |
| 文件 IO | 最快 | `~/` 快，`/mnt/` 慢 5~10 倍 | 正常 |
| 安装成本 | 低 | 中（装 WSL + 配 CUDA） | 低 |
| 适合 | 服务器 / 云主机 | 本地 Windows 开发机 | 只做小模型 LoRA 试水 |

## WSL2 配训练环境

### 1. 显卡驱动：只在 Windows 装

- Windows 侧装 NVIDIA 驱动（新版都带 WSL 支持）；
- **WSL 内不要再装 Linux 显卡驱动**，装了会冲突，`nvidia-smi` 反而坏掉；
- 装完在 WSL 里验证：

```bash
nvidia-smi                     # 应该能看到和 Windows 一样的卡
python -c "import torch; print(torch.cuda.is_available())"
```

### 2. CUDA toolkit 可以不装

PyTorch 的 wheel 自带 CUDA runtime，装 `cu12x` 版本就能跑。
只有要自己编译 flash-attn 之类，才需要装完整 CUDA toolkit。

### 3. 文件放哪：按「读几次」分，不要一刀切

关键区别不是"文件大小"，而是**读的频率和方式**：

| 文件 | 读法 | 放哪 | 理由 |
|---|---|---|---|
| 训练数据（jsonl / arrow） | 每个 epoch 随机读，dataloader 反复扫 | **ext4（`~/`）** | drvfs 随机读开销大，GPU 会被拖到个位数利用率 |
| 训练输出 / checkpoint | 每 N 步写一次，越训越多 | **ext4（`~/`）** | 频繁写；且填满的是虚拟磁盘，不影响 C/D 盘 |
| 虚拟环境 | 每次 import 读大量小文件 | **ext4（`~/`）** | 小文件随机读，drvfs 最不擅长 |
| **模型权重** | **启动时顺序读一次进显存** | **`/mnt/d` 直接用即可** | 只读一次，慢的是那几十秒加载，不是训练 |
| 源码 / 脚本 / 文档 | 偶尔读写 | `/mnt/d` 或 `~/` 都行 | 量小 |

**真实反例（2026-11-15 本机实测）**：为了"避免 drvfs 慢"，把 4.3 GB 的
`Qwen3.5-2B` 从 `/mnt/d/modelscope/` 复制到 `~/Code/sft-case/models/`：

```
cp -r /mnt/d/modelscope/Qwen3.5-2B ~/Code/sft-case/models/
real 1m10.599s          # ≈ 62 MB/s
4.3G                    # 白占 4.3 GB 磁盘
```

而同一次实验里，**直接从 `/mnt/d` 加载这个模型只要 45.1 秒**（320 个张量，
其中偶发单张量卡 18 秒的长尾）。也就是说：

| 做法 | 第一次启动 | 第二次启动 | 磁盘 |
|---|---|---|---|
| 直接用 `/mnt/d` | 45 s | 45 s | 0 |
| 先复制到 ext4 | 70 s + 加载 | 加载（约 10~20 s） | +4.3 GB |

**重启 3 次以上，复制才开始回本**；只跑一两次实验，复制纯亏。

**正例：默认直接用 `/mnt/d`**

```bash
python train_sft.py --model /mnt/d/modelscope/Qwen3.5-2B
```

判断式：

```
复制耗时 < 重启次数 × (drvfs 加载耗时 − ext4 加载耗时)
```

达不到这个条件，就别复制。**先测加载耗时，再决定搬不搬**——这条比"drvfs 一律慢"可靠。

`probe_env.py` 会检查技能包是否在 `/mnt/` 下并给出警告；但**技能包在 `/mnt/` 没关系**
（只放脚本和文档）。真正该放 `~/` 的是数据集、输出和 venv。

### 4. 网络：WSL 默认连不到 Windows 的 127.0.0.1

这是 NAT 模式的老问题：WSL 有独立虚拟网卡，它自己的 `127.0.0.1` 是 WSL 自己，
不是 Windows。所以 Windows 上跑的代理（监听 `127.0.0.1:7890`）在 WSL 里访问不到。

两种办法：

**办法 A：mirrored 网络模式**（本机实测有效）

```ini
# C:\Users\<你>\.wslconfig
[wsl2]
networkingMode = mirrored
```

```powershell
wsl --shutdown          # 必须重启才生效；注意这会杀掉正在跑的训练
wsl sh -c "hostname -I"
```

副作用：改变了 WSL 网络栈，可能影响端口转发、VPN、Docker 网络。出问题改回 `nat`。

**办法 B：大文件交给 Windows 侧下**

本机实测过一个很反直觉的现象：同一个代理，`git ls-remote` 能通，`git clone` 却反复
`GnuTLS recv error (-110)`。根因是 TLS 实现不同——Windows git 用 SChannel 能扛长连接，
WSL git 用 GnuTLS 不行。所以**大仓库/大文件下载走 Windows 侧，WSL 只做本地落地**。

模型权重可以照这个思路：

```powershell
# Windows 侧下到暂存区（走代理/镜像更稳）
huggingface-cli download Qwen/Qwen2.5-7B-Instruct --local-dir D:\Temp\qwen25-7b
```

```bash
# 模型：直接用 /mnt/d 路径即可（只顺序读一次）
#   python train.py --model /mnt/d/Temp/qwen25-7b
#
# 只有当"反复重启训练"实测下来复制更划算时，才搬进 ext4：
#   mkdir -p ~/models && cp -r /mnt/d/Temp/qwen25-7b ~/models/
```

### 5. 换行符：Windows 编辑的脚本在 WSL 里会报 `$'\r'`

```bash
bash train.sh
# bash: ./train.sh: /bin/bash^M: bad interpreter: No such file or directory
```

三种修法，任选：

```bash
dos2unix train.sh                     # 最直接
sed -i 's/\r$//' train.sh             # 没装 dos2unix
```

仓库层面根治：`.gitattributes` 写 `* text=auto eol=lf`（本 skill 所在仓库已经这么配了）。

Python 脚本一般不受影响（解释器容忍 CRLF），但 `.sh` 和 shebang 会。

### 6. 路径对照

| Windows | WSL |
|---|---|
| `D:\Code\skills\llm-sft-trainer` | `/mnt/d/Code/skills/llm-sft-trainer` |
| `C:\Users\18372` | `/mnt/c/Users/18372` |
| （WSL 内部）`~/models` | `\\wsl$\Ubuntu\home\<你>\models` |

在 WSL 里给 Windows 侧路径时，反斜杠要转义或改成正斜杠。

### 7. 端口：WSL 里起的服务，Windows 能访问

在 WSL 里起 vLLM（默认 8000），Windows 浏览器直接 `http://localhost:8000` 就能开
（NAT 模式下 WSL 会自动转发 localhost）。mirrored 模式下也通。

反过来，Windows 上的服务在 WSL 里要访问，就得用 mirrored 或 Windows 的局域网 IP。

### 8. 从 Windows 调 WSL：命令怎么传才不会被吞

`wsl -e bash -lc "..."` 这条命令要穿过三层解析：**Windows shell → wsl.exe → bash**。
每穿过一层，引号就被吃掉一层。这是 Windows + WSL 环境下最费时间的坑。

实测（`09_real_cases.md` 案例 16）：模型用 heredoc 传 12 行 python，
`<<'PYEOF'` 的引号被 wsl 吃掉，bash 把 `PYEOF` 当命令执行，连试三次全失败，
最后报告没写出来。

**三级方案，按复杂度选**：

| 场景 | 写法 | 说明 |
|---|---|---|
| 单行简单命令 | `wsl -e bash -lc "ls -la /mnt/d/x"` | 没有嵌套引号时最安全 |
| 需要内层引号 | `wsl -e bash -lc 'python -c "print(1)"'` | **外层单引号、内层双引号** |
| **多行脚本** | **先写文件，再执行** | 唯一稳的办法，见下 |

多行脚本的正确做法：

```bash
# 1. 用 Write 工具把脚本写到工作目录（不经过 shell 传参）
#    D:/Code/_e2e/check.py
# 2. 再执行（路径换成 WSL 写法）
wsl -e bash -lc "cd /mnt/d/Code/_e2e && /home/<user>/.venv/bin/python check.py"
```

**永远不要**：

- `wsl -e bash -lc "python -c \"...\""` —— 三层引号必炸
- heredoc（`<<'EOF'` / `<<PYEOF`）—— 引号被 wsl 吃掉，bash 会把结束标记当命令
- 在命令行里传中文 —— 编码层再多一道坑

**判断标准**：同一条命令试到第 3 次，就停下来改成「写文件 + 执行」。
这比继续调引号快十倍。

## 常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| WSL 里 `nvidia-smi` 找不到 | Windows 驱动太旧，或 WSL 内装了 Linux 驱动 | 更新 Windows 驱动；卸掉 WSL 内的驱动 |
| dataloader 极慢、GPU 利用率个位数 | 数据在 `/mnt/` | 移到 `~/` |
| `bash: $'\r': command not found` | 脚本是 CRLF | `dos2unix` 或改 `.gitattributes` |
| WSL 里连不上 Windows 代理 | NAT 模式，localhost 隔离 | `.wslconfig` 设 mirrored + `wsl --shutdown` |
| `wsl --shutdown` 后训练没了 | 正常，整个 WSL 被关掉 | 先停训练再 shutdown |
| WSL 磁盘越用越大 | 虚拟磁盘不自动收缩 | 删文件后 `wsl --manage <发行版> --set-sparse` |
| 训练脚本在 Windows 跑得好，WSL 报找不到文件 | 路径写死成 `D:\...` | 用相对路径或 `pathlib` |

## 什么时候别用 WSL

- **只有 AMD / Intel 核显**：WSL 的 GPU 直通主要面向 NVIDIA，AMD 支持有限；
- **公司安全策略禁用虚拟化**：Hyper-V / 虚拟机平台被关，WSL2 起不来；
- **必须用 Windows 独占的驱动或采集卡**：WSL 里看不到。

这几种情况就在 Windows 原生跑，但要接受：`flash-attn` 可能装不上、`deepspeed` 基本别想、
`vllm` 只支持部分版本。方案上就按 Windows 能支持的范围选库，别硬上。

## 下一步

平台定了，回到 `references/01_probe.md` 继续探显存和模型；如果决定进 WSL，
把训练工作目录建在 `~/work/<项目名>/`，不要建在 `/mnt/` 下。
