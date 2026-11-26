# 02 · 定方案：三档精度、长上下文、选哪个库

> **这篇怎么用**：每一节先给「**怎么找依据**」（心法 + 本节的具体判断），
> 再给「**找到了什么**」（三库对照，标注为快照）。
> 快照会过期，心法不会。心法全文在 `06_docs_nav.md`。
>
> 只看结果不看推导，就是在背答案——官网一改组，答案全废，你还不知道为什么废。

## 这一步的产出

一份 `02_training_plan.md`，里面每个决策都要写三行：

```
选了什么 → 为什么不选别的 → 依据哪篇文档（关键词）+ 怎么验证
```

只写"用 LoRA"不算方案，那是口号。

## 一、三档精度：全参 → LoRA → QLoRA

### 决策树

```
全参估算 ≤ 可用显存 × 0.85 ？
├── 是 → 全参（质量上限最高，产出一个完整模型）
└── 否 → bf16 基座 + LoRA 估算 ≤ 0.85 ？
         ├── 是 → LoRA（默认，工业界最常用）
         └── 否 → 4bit 基座 + LoRA（QLoRA，兜底）
                  └── 还不行 → 降 batch / 降 seq_len / 上多卡 ZeRO
```

`0.85` 是留给 KV cache、CUDA context 和碎片的余量。别按 100% 算。

### 三档对比（背下来）

| | 全参 | LoRA | QLoRA |
|---|---|---|---|
| 训练显存（7B） | ~112 GiB | ~17 GiB | ~6 GiB |
| 产出 | 完整模型 | adapter | adapter |
| 训练速度 | 基准 | 略快 | 慢 30~50% |
| 精度 | 上限最高 | 接近全参 | 比 LoRA 低 0.5~1.5 分 |
| vLLM 部署 | 直接加载 | bf16 base + adapter | 同 LoRA（见下表） |
| 多任务 | 一个任务一个模型 | 一个 base 挂多个 adapter | 同 LoRA |

### 必须写进方案的两句话

1. **QLoRA 的 4bit 只存在于训练阶段**。部署时 vLLM 加载的是 bf16 基座 + adapter
   （或 merge 后的完整模型），不是那个 4bit 训练基座。
2. **LoRA 兼容 vLLM 不是自动的**，取决于 base 架构是否被 vLLM 支持、adapter 类型、
   vLLM 版本。必须按 `05_verify.md` 的部署清单实测。

### 依据怎么找（规则，不是答案）

**这里不告诉你"该看哪一篇"。** 给你一条规则，换任何库都能用——包括你没见过的库。

#### 规则：目录名里的功能词决定它的用途

| 目录名里出现这些词（英/中） | 目录类型 | 里面装什么 | 对应哪类问题 |
|---|---|---|---|
| concept / architecture / 原理 / 概念 / 架构 | 概念 | 为什么成立 | 判断可行性 |
| how-to / guide / tutorial / recipe / practice / 指南 / 教程 / 最佳实践 | **配方** | 一个目标一篇，给一组参数 | **B 怎么配** |
| blog / case / example / benchmark / 案例 / 实战 | **实证** | 带数字的战报 | **C 值多少** |
| reference / api / command / cli / 参数 / 手册 / instruction | **手册** | 开关清单，查而不读 | **A 有没有** |
| trainer / algorithm / method / 算法 / GRPO / DPO | 算法 | 一个算法一篇 | 选算法 |
| model / 模型 | 对象 | 按模型组织 | "这个模型怎么训" |
| install / get-started / 安装 / 入门 | 入口 | 最小可跑 | 第一次跑通 |

规则里**没有一个库的名字**。官网把 `blog` 改成 `case-studies`、把 `how_to_guides` 改成 `guides`，
规则照样命中——**这就是它和"结果表"的区别**。

（这里只列了跟本节相关的类型；集成 / 部署 / 答疑 / 实验 / 定制等完整表见
`06_docs_nav.md` 第二层，脚本里就是 `DIR_WORDS` 那个词表。）

#### 用规则判这一节的问题

这一节的问题（显存、LoRA 超参、全参还是 LoRA、多卡、训练慢）**都是 B 类**，个别带 C 类。
所以只要两类目录：**配方**（怎么配）+ **实证**（能到多少）。

```bash
python scripts/docs_outline.py --dirs --type 配方     # 219 tok
python scripts/docs_outline.py --dirs --type 实证     # 44 tok
```

真实输出（**类型是规则算的，不是我写死的**）：

```
=== trl（57 页 / 10 个目录，只看「配方」）===
    03_how_to_guides/    10  配方

=== unsloth（202 页 / 34 个目录，只看「配方」）===
      fine-tuning-llms-guide/                 4  配方
      tutorials/                             37  配方
      ...（共 10 条）

=== swift（75 页 / 10 个目录，只看「配方」）===
    BestPractices/         20  配方

=== trl（57 页 / 10 个目录，只看「实证」）===
    05_examples/     3  实证

=== unsloth（202 页 / 34 个目录，只看「实证」）===
    blog/    13  实证
```

（`swift` 那一段没有输出——它**没有**实证类目录。这不是命令坏了，是事实。）

**读出来的三件事**（这才是推导，不是答案）：

1. trl 的配方**只集中在一个目录**（`03_how_to_guides` 10 页）→ 它把"问题"当独立一章做 → 强项是**覆盖全**。
2. unsloth 的配方**散在 10 个目录** → 按场景/模型组织 → 强项是**具体模型的具体做法**。
3. swift **没有实证目录**（`--type 实证` 只命中 trl 和 unsloth）→ 这是真空，不是遗漏 → **要数字只能去别的库找**。

#### 判读对不对？抽一页验证，别信标签

标签是启发式，会错。花 100 tok 验证：

```bash
python scripts/docs_outline.py --toc --in 03_how_to_guides
#  看标题是不是"一个问题一篇"：Reducing Memory Usage / Speeding Up Training / ...
#  是 → 判读成立；全是 SFT/DPO/GRPO 这种算法名 → 说明判错了，退回去看 --dirs 全表
```

#### 这一节的"答案"在哪

不在本文里。跑：

```bash
python scripts/docs_outline.py --recipes --step 3
```

那是**某次按上面规则跑出来的缓存**（带日期），省你重跑；过期就按规则重来。
**缓存能抄，规则能迁移——只有规则是你的。**

## 二、长上下文：阶梯探测，不要拍脑袋

### 为什么要阶梯

文档写"支持 128k"，指的是模型结构上限，不是你机器能跑的上限。
真实可用长度由显存、注意力实现、并行策略共同决定，**只能测**。

### 探测方法：粗扫 + 细调

```
粗扫（找区间）：10000 → 16000 → 24000 → 32000 → 48000 → 64000
                 每一步都跑一次 smoke test（20~50 步）
细调（找边界）：在「最后一个成功」和「第一个失败」之间，每次 +500
```

为什么不是一路 +500：从 10k 到 64k 要 108 次实验，纯浪费。
粗扫 6 次就能把区间缩到 1.5 倍以内，再用 +500 逼近，通常 10 次以内收敛。

### "满足"的三个判据（必须同时满足）

| 维度 | 判据 | 怎么测 |
|---|---|---|
| 显存 | 峰值 ≤ 可用 × 0.90 | smoke test 里 `torch.cuda.max_memory_allocated()` |
| 速度 | ≥ 你定的下限（如 500 tokens/s） | 训练日志的 tokens/s |
| 精度 | 固定探针集上，loss 相对基线涨幅 ≤ 1% | 同一批 20~50 条样本，每次同一口径 |

**没有固定探针集，"不丢精度"就是一句空话。** 探针集在第 1 步就要定好。

### 控制变量：一次只动 seq_len

阶梯探测时，下面这些都**固定不动**，否则测出来的上限不是 seq_len 的上限：

- packing 开/关
- batch size、gradient accumulation
- gradient checkpointing
- 并行策略（单卡 / ZeRO / TP）

### 四种取舍，其实是一个选择题

你说的四种情况（长上下文 × 精度 × 速度），落到配置上就是：

| 目标 | 典型手段 | 代价 |
|---|---|---|
| 长 + 不丢精度 + 不降速 | 更快的注意力实现（FlashAttention / 专属 kernel）+ 足够显存 | 最贵，通常要更大卡 |
| 长 + 不丢精度 + 可降速 | 梯度检查点、序列并行 | 慢 20~40%，显存省很多 |
| 长 + 不降速 + 可损精度 | 4bit 训练（QLoRA）、降低注意力精度 | 精度掉 0.5~1.5 分 |
| 长 + 降速 + 损精度 | 上面两条叠加 | 最省显存，质量最差 |

这四条不是"四种训练方法"，是**同一个训练脚本的四种参数组合**。
所以方案里要写清楚：这次选哪一格，以及为什么这一格够用。

### 依据怎么找

**这里有个最容易混的地方，正好演示心法第 1 层：**

- 「这个模型支持 128k 吗」→ **A 类（有没有）**，答案在 config / 参数表
- 「我的卡能跑多长」→ **C 类（值多少）**，答案只能靠实测，任何文档都给不了

两句话听起来是一件事，其实分属两类。**拿 A 类的答案当 C 类的结论，就是「文档说支持 128k，我跑 8k 就 OOM」的由来。**

按 `06_docs_nav.md` 第二层的功能词规则，这两类分别对应：

| 你要的 | 类型 | 去哪种目录 | 命令 |
|---|---|---|---|
| 支持多长（能力上限） | A | 手册 / 对象 | `--dirs --type 手册` |
| 怎么配到这个长度 | B | 配方 | `--dirs --type 配方` |
| 别人在什么配置下跑到了多少 | C | 实证 | `--dirs --type 实证` |

```bash
python scripts/docs_outline.py --dirs --type 实证
python scripts/docs_outline.py --dirs --type 配方
```

现成结果（缓存，带日期）：`python scripts/docs_outline.py --recipes --step 3`。

**注意定位**：三个来源给的都是**参照**，不是你的结论。真实可用长度必须靠下面的阶梯探测。
文档说 500K，只说明"那个模型+那个卡"做到了 500K。

## 三、选库与组合：不是三选一，是分工

先纠正一个常见误判：这三个库不是竞品。

| | trl | unsloth | swift |
|---|---|---|---|
| 是什么 | HF 官方 trainer 库 | 内核 + 显存优化层 | 独立一站式 CLI 框架 |
| 关系 | 参数名的事实标准 | 插在 transformers/trl 之上 | 自成体系，可用 `--tuner_backend unsloth` 借内核 |
| 强项 | 算法最全、API 权威 | 省显存、提速、4bit 最顺 | 一条命令、国产卡、多模态、自带评测部署 |

完整对照、证据和分工矩阵见 **`08_cross_lib.md`**，这里只给判断顺序。

### 综合判断四问（按顺序问，别跳）

1. **有唯一解吗？** 国产卡 → swift；500k 级别 → unsloth；要最新算法 → trl。有唯一解就锁死，不做无意义横评。
2. **最简能跑通的是哪条？** 先用它验证数据、模板、环境（通常是 swift 一条命令）。
3. **上限最高的是哪条？** 正式训练用这条（通常是 trl 或 unsloth+trl），并写清从"最简"到"最强"的升级路径。
4. **能组合吗？哪层归谁？** 加载 / 训练可以分家；swift 是整包替代，不能混进同一个进程。

### 三条固定组合（照抄，别自己发明）

| 组合 | 写法 | 适合 |
|---|---|---|
| A 最省显存（默认） | unsloth `FastLanguageModel` 加载 + trl `SFTTrainer` | 单卡 / 小多卡，LoRA / QLoRA |
| B 一条命令试水 | `swift sft --tuner_backend unsloth ...` | 验证数据与模板，别当最终方案 |
| C 全参 / 多卡 | trl + DeepSpeed；或 swift `--deepspeed zero3` | 全参放得下、需要分片 |

组合 A 的完整代码在 `08_cross_lib.md`；保存出来是标准 HF 权重，vLLM 直接能加载。

### 组合禁忌（都有出处）

1. swift + unsloth 后端时，`--target_modules` **不能写 `all-linear`**（会 assert 失败），必须写 `q_proj k_proj v_proj` 这类具体名 —— swift FAQ Q50。
2. 不要在同一进程既 patch unsloth 全局、又走 swift 整包。
3. 三库同名参数默认值不同：`learning_rate`（swift 全参 1e-5 / LoRA 1e-4）、`lora_alpha`（swift 32 / trl 示例 16）、`max_length`。照搬名字不改值 = 静默改方案。

### 依据怎么找（这一类最特殊）

上面两节是 A/B/C 类问题，答案都在**某一篇页面**里。
「三库怎么组合」是 **D 类**——答案不在任何一篇里，在**两个库的接口处**。

判断标准只有一条：**必须看到对方库的名字出现在文档里**。
不能靠「都是训 LoRA 的，应该能搭」这种推理。

```bash
grep -rn "unsloth" docs/trl --include=*.md | head        # trl 里有没有 unsloth 的入口
grep -rn "from trl import" docs/unsloth --include=*.md   # unsloth 示例用不用 trl
grep -rn "tuner_backend" docs/swift --include=*.md       # swift 能不能指定后端
```

**实测三条都命中**（2026-11-16）：

```
docs/trl/04_integrations/unsloth_integration.md          ← 官方集成页
docs/unsloth/basics/finetuning-from-last-checkpoint.md:70: from trl import SFTConfig, SFTTrainer
docs/swift/Instruction/Command-line-parameters.md:12: 🔥tuner_backend: 可选为'peft'，'unsloth'
```

三条齐了，才敢说"能组合"；命不中就说明不能搭，别硬试。

```bash
python scripts/docs_outline.py --recipes --step 5   # 组合场景的详细版
python scripts/docs_outline.py --matrix             # 一眼看三库覆盖与最简/最强
```

## 四、vLLM 部署兼容性（决策时就要想清楚）

| 部署方式 | 支持 | 说明 |
|---|---|---|
| bf16 基座 + `--enable-lora` 挂 adapter | ✅ 标准 | 最稳，多 adapter 共享 base |
| merge 成 bf16 完整模型再服务 | ✅ 最稳 | 单模型场景首选 |
| 4bit bnb 基座 + adapter，offline `LLM()` | ✅ | vLLM 原生集成的模型可用 |
| 4bit bnb 基座 + `vllm serve --enable-lora` | ⚠️ 按版本实测 | 在线服务 / 多 LoRA / TP 组合有限制 |
| 4bit GPTQ/AWQ/FP8 基座 + adapter | ✅ 部分 kernel | 这是**推理量化**，和训练的 NF4 不是一回事 |

方案里必须写：**部署走哪一行，以及怎么验证**（见 `05_verify.md`）。

### 依据怎么找

**这一节演示心法第 3 层的边界：标题措辞会骗人，必须看正文确认。**

三库里都有一篇叫 `vLLM ...` 的页，但它们讲的**不是同一件事**：

| 库 | 标题 | 正文实际讲什么 | 能不能指导部署 |
|---|---|---|---|
| unsloth | `vLLM Deployment & Inference Guide` | adapter → 起服务的完整流程 | ✅ 能直接抄命令 |
| swift | `推理和部署` | 多后端（vLLM / SGLang / LMDeploy）+ 国产卡 | ✅ 能 |
| trl | `vLLM` | **训练中用 vLLM 加速生成**（RL 采样），不是部署 | ❌ **不能**，拿它指导部署会走错 |

只看标题三篇都像部署文档；只有打开看第一个 `##` 才分得清。
这就是「**标题给候选，正文给结论**」——心法第 3 层不是让你跳过正文，是让你**用标题缩小到 1~3 篇，再花 30 秒确认它到底讲什么**。

**结果（快照：2026-11-16）**

| 库 | 文档 | 它解决什么 |
|---|---|---|
| unsloth | `vLLM Deployment & Inference Guide` | 从 adapter 到服务的完整路径，命令能直接抄 |
| unsloth | `LoRA Hot Swapping Guide` | 一个 base 挂多个 adapter 的配置与限制 |
| swift | `推理和部署` | 多后端（vLLM / SGLang / LMDeploy）+ 国产卡 |
| trl | `vLLM` | **不是部署文档**：训练中用 vLLM 加速生成，别拿它指导部署 |

**过期了重跑**：

```bash
python scripts/docs_outline.py --search vllm
# 命中后必须做这一步，别只看标题：
python scripts/docs_outline.py --headings <命中的路径>
```

## 五、方案模板

```markdown
# 训练方案

## 1. 目标
- 任务：<一句话>
- 成功标准：<可测的指标，如探针集准确率 ≥ 85%>

## 2. 精度档位
- 选：LoRA（bf16 基座）
- 不选全参：全参估算 116 GiB > 4×80G 单卡可用，ZeRO-3 成本过高
- 不选 QLoRA：17.5 GiB 在单卡 80G 内宽裕，没必要牺牲精度
- 依据：trl `PEFT` + unsloth `Is Fine-tuning Right For Me`（LoRA vs QLoRA 一节）+ swift `--tuner_type` 参数说明
- 验证：smoke test 实测峰值显存 ≤ 70 GiB

## 3. 上下文
- 目标：不丢精度、可接受 20% 降速
- 手段：梯度检查点 + 粗扫到 32k，再 +500 细调
- 依据：trl `Training Beyond 1M Tokens`（原理）+ unsloth `500K Context Length Fine-tuning`（显存实现）+ swift 数据参数节（截断策略）
- 验证：32k 下峰值 ≤ 72 GiB，tokens/s ≥ 500，探针集 loss 涨幅 ≤ 1%

## 4. 库与组合（三库对照，不许只写一个）
| 决策点 | trl | unsloth | swift | 采用 | 理由 |
|---|---|---|---|---|---|
| 加载 / 量化 | - | FastLanguageModel 4bit | quant_method | unsloth | 单卡省显存，实测峰值最低 |
| 训练循环 | SFTTrainer | 复用 trl trainer | 自带 trainer | trl | 回调/日志/ckpt 可控 |
| 显存优化 | Reducing Memory Usage | 500K blog | 参数表 | 两者叠加 | 4bit + 检查点 + packing |
| 评测 | 无专页 | 无 | `评测` | swift | 唯一成体系 |
- 为什么不用另外两条：swift 定制算法难；纯 trl 显存压不到 8k
- 组合方式：A（unsloth 加载 + trl trainer），见 08_cross_lib.md

## 5. 部署
- bf16 base + adapter，vLLM `--enable-lora`
- 验证清单见 05_verify.md

## 6. 候选对比（候选 ≥3 个时必填，少于 3 个写"只有 A/B 两个，直接比了"）
- 选型图：`plan.png`（`plot_plan.py` 生成，见 12_tradeoff.md 4.4）
- 硬约束：显存 ≤ ___ GiB ｜ 时长 ≤ ___ h ｜ 精度 ≥ ___%
- 越界被淘汰的：<方案 → 差多少>
- 最终选的：<方案>，理由是<哪一个维度真的紧 / 还是哪一维不紧所以不必省>
- **唯一卡住的维度**：<显存 / 时间 / 精度> —— 只有它值得优化，另外两个是护栏
```

**第 6 节为什么必须有**：前 5 节说的是"我选了什么"，只有第 6 节说"**为什么不是别的**"。
没有这一节，方案看起来永远正确——因为你看不到被淘汰的那些差在哪。
