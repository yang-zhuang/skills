# 01 · 探环境：把所有"应该够"变成数字

## 为什么先做这一步

显存不够，后面所有方案都是空谈。这一步只干一件事：**把机器和模型的真实情况变成数字**，
不给建议（建议在第 3 步）。

普通人容易犯的错：拿"显卡总量"当"可用显存"。24 GB 的卡上可能已经躺着别人的进程，
真正能用的只有 18 GB。**只看空闲。**

## 0. 先看平台：Linux / WSL2 / Windows

`probe_env.py` 的第一段就是平台，判断规则只有一条：

**Linux 原生 > WSL2 > Windows 原生。**

- 在 WSL 里：推荐，基本等同 Linux。CUDA 由 Windows 驱动透传，WSL 内别装驱动。
- 在 Windows 且装了 WSL：训练进 WSL 跑，Windows 只负责下载和编辑。
- 在 Windows 没装 WSL：小模型 LoRA 可以先跑，但 flash-attn / deepspeed / vllm 别指望。

文件该放哪、代理怎么通、CRLF 怎么处理、端口怎么访问 —— 见 `references/07_platform.md`。

## 两套环境，别混

| 环境 | 干什么 | 放哪 | 装什么 |
|---|---|---|---|
| 技能环境 | 跑爬虫、读文档 | 技能包下 `.venv/` | 只有 `requests` |
| 训练环境 | 跑 torch / trl / unsloth | **每个训练工作目录**下 `.venv/` | torch + 训练库，几 GB |

为什么分开：torch 和 CUDA 版本很挑，不同模型可能要不同版本；混在一个环境里，
装第二个库时经常把第一个搞坏。分开后哪个坏了删哪个，不牵连。

### 真实反例：为了省下载，借了别的环境的 torch（2026-11-15 实测）

WSL 里已经有一个 conda 环境 `vllm`（`torch 2.13.0+cu130` / `transformers 5.16.1`），
而 conda base 是空的（Python 3.13.5，torch/transformers 全 `MISSING`）。

当时为了省 2~3 GB 下载，用了这个写法：

```bash
python -m venv --system-site-packages .venv   # 反例
```

看起来"隔离"了，实际是**隐性耦合**：

- 这个 `.venv` 里的 `import torch` 一直指向 `vllm` 环境的包；
- 哪天有人升级 / 卸载 `vllm`，或者它换了 torch 版本，本项目直接崩；
- 报错栈里的路径在另一个环境，排查时极难想到；
- 两个项目共用一份 torch，A 要 cu130、B 要 cu128 时互相拖累。

**正例：项目本地、完全独立**（多花一次下载，换掉全部上述风险）：

```bash
/usr/bin/python3 -m venv .venv        # 不继承任何已有环境
.venv/bin/pip install torch           # 自己装一份
```

判断标准很简单：`rm -rf .venv` 之后，现有环境应该**毫发无损**；反过来，
删掉任何现有 conda 环境，本项目也应该还能跑。做不到这两条，就不叫隔离。

### venv 建不出来的两种真实情况

| 现象 | 原因 | 做法 |
|---|---|---|
| `Failing command: .../bin/python3`（ensurepip 失败）| Ubuntu 没装 `python3-venv`，且无 sudo 权限 | `python3 -m venv --without-pip .venv` + `get-pip.py` |
| `No module named pip` | 系统 Python 是精简安装 | 同上，先把 pip 装进 venv，再动系统 |

```bash
python3 -m venv --without-pip .venv
curl -sSL https://bootstrap.pypa.io/get-pip.py -o get-pip.py
.venv/bin/python get-pip.py -i https://pypi.tuna.tsinghua.edu.cn/simple
```

**禁止用 uv**（本机约定），一律 `python -m venv`：

### 装完占多少、怎么删干净（2026-11-15 实测）

| 东西 | 位置 | 体积 | 怎么清理 |
|---|---|---|---|
| torch + nvidia CUDA 运行库 | `<项目>/.venv/lib/python3.12/site-packages/nvidia/` | **3.0 GB** | `rm -rf .venv` |
| 整个训练环境 | `<项目>/.venv/` | **6.0 GB** | 同上，删完不留痕 |
| pip 下载缓存（全局） | `~/.cache/pip/` | **45 GB** | `pip cache purge` |
| conda 环境（本项目没用） | `~/miniconda3/` | 21 GB | 不动它 |

关键点：**CUDA 运行库是以 wheel 形式装进 venv 的**（`nvidia-cudnn-cu13`、`nvidia-cublas` 等），
不是系统级安装，`/usr/local/cuda` 没被碰。所以"想回收空间"就是删 `.venv`。

真正容易忽略的是 pip 缓存：它按 wheel 存一份全局副本，装十个环境就存十份。
磁盘紧张时先 `pip cache purge`，比删环境见效快得多。

```bat
:: 技能环境（一次性）
cd /d D:\Code\skills\llm-sft-trainer
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

:: 训练环境（每个工作目录一份）
cd /d <训练工作目录>
python -m venv .venv
.venv\Scripts\python.exe -m pip install torch transformers datasets peft accelerate bitsandbytes
```

Linux 把 `.venv\Scripts\python.exe` 换成 `.venv/bin/python` 即可。

## 探测顺序

### 1. 机器

```bash
python <skill>/scripts/probe_env.py
```

看三样：

- **卡数 × 单卡空闲显存**（不是总量）
- **算力**：8.0 以上才支持 bf16；7.5（Turing）只能 fp16
- **库版本**：torch / transformers / trl / unsloth / bitsandbytes 是否齐

如果输出说"当前解释器没装 torch"，说明你在技能环境里跑，正常；
到训练环境再跑一遍。

### 2. 模型

模型从哪来，按这个顺序：

| 情况 | 做法 | 注意 |
|---|---|---|
| 用户给了本地路径 | 直接用 | 检查目录里有 `config.json` 和权重文件 |
| 用户给了 HF id | 先看本地有没有缓存；没有就下 | **必须告诉用户下到哪** |

下载位置规则（不许静默丢进默认缓存）：

```bash
# 下到技能包的 models/ 下，目录名带 org，避免同名冲突
python -c "from huggingface_hub import snapshot_download; \
snapshot_download('Qwen/Qwen2.5-7B-Instruct', local_dir='<skill>/models/Qwen__Qwen2.5-7B-Instruct')"
```

下载前先打印：**目标绝对路径 + 预计体积**。7B bf16 约 15 GB，先确认磁盘够
（`probe_env.py` 会报可用空间）。

### 3. 模型结构 + 显存粗估

```bash
python <skill>/scripts/probe_model.py ./models/Qwen__Qwen2.5-7B-Instruct --seq-len 4096 --batch 1
```

输出里有：

- 层数 / hidden / kv_heads / 是否 MoE —— 这几个决定后面怎么调
- 参数量
- 全参 / LoRA / QLoRA 三档显存与判定

**三个必须知道的误差来源**：

1. 激活显存是经验系数，±30%，长序列更不准；
2. 没算 KV cache、CUDA context、显存碎片；
3. MoE 模型的参数量含全部专家，估算会明显偏高。

所以：**判定只是初筛，能不能跑必须 smoke test**（见 `04_train.md`）。

## 用户已经说了，也要复核

用户说"我有 4 张 A100"，你仍然要跑 `probe_env.py`，因为可能：

- `CUDA_VISIBLE_DEVICES` 只放出了 1 张；
- 卡被别的进程占了一半；
- 驱动和 torch 版本不匹配，torch 根本看不到卡。

用户说"这模型能全参"，你仍然要跑 `probe_model.py` + smoke test，因为可能：

- 他把"LoRA 能跑"记成了"全参能跑"；
- 他用的 seq_len 是 1024，你要用 8192。

复核不是不信任，是**把口头信息变成可复现的数字**。

## 产出物：`01_env_report.md`

```markdown
# 环境报告

## 机器
- 卡：4 × A100-SXM4-80GB，单卡空闲 79,200 MiB
- 算力：8.0（支持 bf16）
- 驱动：550.54 / torch 2.4.1+cu121

## 模型
- Qwen/Qwen2.5-7B-Instruct，本地路径 `<绝对路径>`
- 28 层 / hidden 3584 / 28 heads / 4 kv_heads
- 参数量 7.62 B

## 显存判定（seq_len=4096, batch=1）
| 方案 | 估算 | 判定 |
|---|---|---|
| 全参 | 116 GiB | 单卡放不下，4 卡 ZeRO-3 可试 |
| LoRA | 17.5 GiB | 单卡可，推荐 |
| QLoRA | 6.2 GiB | 单卡宽裕 |

## 待验证
- [ ] smoke test 实测 LoRA 峰值显存
- [ ] 确认 4 张卡都可见（nvidia-smi 与 torch 一致）
```

## 常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| `torch.cuda.is_available()` 为 False | 装的是 CPU 版 torch | 重装对应 CUDA 版本的 torch |
| nvidia-smi 有卡，torch 看不到 | `CUDA_VISIBLE_DEVICES` 或驱动/torch 版本不匹配 | 先 `echo $CUDA_VISIBLE_DEVICES` |
| 显存总量够但一跑就 OOM | 没算激活和碎片 | 开梯度检查点 / 降 seq_len / 上 QLoRA |
| 下载模型到一半磁盘满 | 没先看可用空间 | 先 `probe_env.py`，再决定下哪个精度 |

## 下一步

拿着 `01_env_report.md` 去 `references/02_decide.md`，按数字选方案。
