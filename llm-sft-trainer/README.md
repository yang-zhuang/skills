# llm-sft-trainer

训练一个模型之前，先按这套流程走。

## 这个技能解决什么问题

大部分人训模型卡住，不是因为不会写代码，是因为**顺序乱了**：

- 一上来就抄个训练脚本，跑到一半 OOM，才发现显存不够；
- 把 trl 文档从第一页翻到最后一页，还是不知道该配什么；
- 训完 loss 很好看，一推理发现全是废话，因为数据格式或 label mask 错了。

这个技能把"一个有经验的科研工作者会怎么做"固化成七步：定任务 → 探环境 → 定方案 →
备数据 → 写脚本 → smoke test → 训练 → 验收。每一步都有产出物和通过条件，不通过就停。

它**不直接给答案**，而是告诉你去哪份文档的哪一节找答案，然后逼你实测验证。

## 安装

技能包是自包含的：脚本自带，文档运行时下载。

WorkBuddy 默认从 `~\.workbuddy\skills\` 读技能，而源码在 `D:\Code\skills\`。
二选一：

```bat
:: 方式 A：软链（推荐，改源码立即生效；需要开发者模式或管理员）
mklink /D "%USERPROFILE%\.workbuddy\skills\llm-sft-trainer" "D:\Code\skills\llm-sft-trainer"

:: 方式 B：复制（简单，但改源码后要重新复制）
robocopy "D:\Code\skills\llm-sft-trainer" "%USERPROFILE%\.workbuddy\skills\llm-sft-trainer" /E
```

## 第一次使用

```bat
cd /d D:\Code\skills\llm-sft-trainer

:: 1) 建技能环境（只装 requests）
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

:: 2) 同步三个库的官方文档（约 1~3 分钟）
.venv\Scripts\python.exe scripts\sync_docs.py
```

同步完成后 `docs/` 下会有 `trl/`、`unsloth/`、`swift/` 三棵目录和各自的 `INDEX.md`。
**文档不进 git**，换机器重新跑一次同步即可。

## 它怎么用（一个完整例子）

> 用户：帮我把 Qwen2.5-7B 用这批客服对话微调一下，单卡 4090。

| 步 | 动作 | 产出 |
|---|---|---|
| 1 | 问清楚目标、数据量、怎么算成功、**多快算够** | `00_task_profile.md` |
| 2 | `probe_env.py` + `probe_model.py` | 24G 卡、7.6B 模型、全参要 116G → 不可能 |
| 3 | 按数字选 LoRA（17.5G 估算），上下文先 4k，画一张候选对比图 | `02_training_plan.md` + `plan.png` |
| 4 | `check_dataset.py` 看 token 分布和格式 | `03_dataset_report.md` + 转换后数据 |
| 5 | 生成 `train.sh`，跑 30 步 smoke test | 实测峰值 19.2G、s/step 3.2，通过 |
| 6 | 全量训练 | `outputs/` |
| 7 | 探针集评测 + vLLM 部署验证 + **成本核算** | `05_eval_report.md` |

## 为什么三个库要一起看

不是三选一，是分工：

- **trl** 管训练循环（SFTTrainer，参数名的事实标准）
- **unsloth** 管加载、4bit、内核加速、省显存
- **swift** 是整包方案（一条命令、国产卡、多模态、自带评测与部署）

覆盖范围也不同：评测只有 swift 有专章，500k 上下文只有 unsloth 有实测，
trl 的 vLLM 页讲的是"训练中加速生成"而不是部署。只看一个库，容易漏掉更简单的路或更高的上限。

一条命令看三库覆盖与结论：

```bat
.venv\Scripts\python.exe scripts\docs_outline.py --matrix
.venv\Scripts\python.exe scripts\docs_outline.py --recipes --step 3
```

默认组合（单卡 LoRA）：**unsloth 加载 + trl SFTTrainer**，保存出来是标准 HF 权重，vLLM 直接能加载。
详见 `references/08_cross_lib.md`；真实踩坑记录见 `references/09_real_cases.md`。

## 读文档的四层阶梯（每一层先看代价）

334 页正文合计约 **1.59M tok**，整库读是不可能的。但**文件夹结构只有 57 行，约 720 tok**，
所以正确顺序是逐层收窄，而不是一上来就打开文档：

| 层 | 命令 | 实测代价 | 用来决定 |
|---|---|---|---|
| L0 文件夹结构 | `--dirs` | **57 行，约 720 tok**（带类型标注） | 去哪个目录 |
| L1 某目录的页面清单 | `--toc --in <目录>` | 单库 874 tok / 全量 7.0k | 看哪一篇 |
| L2 页内标题 | `--headings <页>` | 全量到 `##` 19.6k、`####` 58k | 读哪一节 |
| L3 正文 | `--read <页>` | 全量 1.59M，**必须分块** | 具体内容 |

`--read` 内置 10k 预算规则：**整页 ≤ 10k tok 一次读完；> 10k 只给第 1 块**，
并告诉你下一块的命令。切点回退到空行/标题行，不会把句子劈开。

```bat
.venv\Scripts\python.exe scripts\docs_outline.py --dirs
.venv\Scripts\python.exe scripts\docs_outline.py --toc --in how_to_guides
.venv\Scripts\python.exe scripts\docs_outline.py --headings docs/trl/03_how_to_guides/reducing_memory_usage.md
.venv\Scripts\python.exe scripts\docs_outline.py --read docs/swift/Instruction/Command-line-parameters.md
```

## 依赖

| 环境 | 装什么 | 放哪 |
|---|---|---|
| 技能环境 | `requests` | 技能包 `.venv/` |
| 训练环境 | `torch` + `transformers` + `trl`/`unsloth`/`ms-swift` + `peft` + `bitsandbytes` | **每个训练工作目录**下 `.venv/` |

不用 uv，一律 `python -m venv`。两套环境分开的原因见 `references/01_probe.md`。

## 目录

```
llm-sft-trainer/
├── SKILL.md                 给 AI 看的入口
├── README.md                本文件
├── scripts/
│   ├── sync_docs.py         三库文档同步
│   ├── docs_outline.py      目录结构导出（离线）
│   ├── probe_env.py         平台/GPU/库探测
│   ├── probe_model.py       模型结构与显存粗估
│   ├── check_dataset.py     数据集体检
│   ├── plot_plan.py         选型图：显存×时长散点 + 硬约束 + 帕累托（需 matplotlib）
│   ├── selftest.py          自测：四个探针，分钟级（可选）
│   └── selftest_e2e.py      自测：完整七步，会真训练，小时级（可选）
├── references/              15 篇流程文档 + recipes.json（缓存）
├── assets/img/              选型图示例（**给人看的，不要 Read**）
├── docs/                    运行时下载的官方文档（不入库）
└── models/                  可选：模型权重（不入库）
```

## 边界（它不做什么）

- **不做强化学习**：DPO / GRPO / PPO 只留了查文档的入口，没有实现流程。
- **不自动跑训练**：全量训练、下载大模型前会停下来让你确认。
- **显存是粗估**：权重部分准，激活部分误差 ±30%，最终以 smoke test 实测为准。
- **只覆盖三个库**：trl / unsloth / ms-swift；别的库可以照着流程走，但导航表不覆盖。
- **平台建议不是强制**：Windows 上会建议进 WSL，但不会替你装；原生 Windows 也能跑小模型 LoRA。

## 文档更新了

```bash
python scripts/sync_docs.py --only unsloth   # 增量，只下变化的页
python scripts/sync_docs.py                  # 三库全量
```

索引每次按官网清单重新生成，所以官网改组章节不需要改技能。
