# 08 · 三库综合：谁最简、谁最强、怎么组合

## 为什么必须三个一起看

只看一个库，会犯三种错：

1. **看不到更简单的路**：swift 一条命令能验证的事，用 trl 手写一百行。
2. **看不到上限**：unsloth 的 500k 不是某个参数，是 loss 分块 + 检查点增强 + Tiled MLP 的组合拳，只看参数表会以为改个 `max_seq_length` 就行。
3. **组合时踩坑**：swift 用 `--tuner_backend unsloth` 时，`--target_modules all-linear` 会直接 assert 失败——这条只写在 swift 的 FAQ 里，看 trl/unsloth 永远看不到。

反过来，只看 swift 也不够：它的参数表给的是"能调什么"，不解释"为什么"，遇到长上下文崩溃、loss 不降，还得回 trl 那篇看原理。

## 三库的真实关系：不是三选一

| | trl | unsloth | swift |
|---|---|---|---|
| 定位 | HF 官方 trainer 库 | 内核 + 显存优化层 | 独立一站式 CLI 框架 |
| 和别人的关系 | 参数名的事实标准 | 插在 transformers/trl 之上 | 自成体系，自带 trainer；可用 `--tuner_backend unsloth` 借内核 |
| 强项 | 算法最全、API 权威、生态最标准 | 省显存、提速、4bit/QLoRA 最顺 | 一条命令、国产卡、多模态、中文参数表、自带评测与部署 |
| 弱项 | 不管 kernel，显存吃紧要自己配 | 只支持列表内模型，跨框架易冲突 | 定制算法难，参数名自成一套 |
| 文档形态 | 概念 + API 参考 | 实操 notebook / blog | 参数总表 + 最佳实践 |

「文档形态」那行不是印象，是 `06_docs_nav.md` 第二层的功能词规则跑出来的：
`conceptual_guides`/`api` → 概念/手册、`blog` → 实证、`Instruction`+`BestPractices` → 手册+配方。
官网改目录名也推得出同样结论。

**可核对的三条证据**（都能自己跑，不用信我）：

```bash
grep -rn "unsloth" docs/trl --include=*.md | head        # → 有 unsloth_integration 页
grep -rn "from trl import" docs/unsloth --include=*.md   # → unsloth 示例直接 import trl
grep -n "tuner_backend" docs/swift/Instruction/Command-line-parameters.md
#  → 第 12 行：tuner_backend: 可选为'peft'，'unsloth'。默认为'peft'。
```

所以准确说法是：**unsloth 是加速层，trl 是训练层，swift 是整包方案**。前两个天然可组合，swift 是替代关系。

## 分工矩阵：按层看，不按库看

| 层 | 首选 | 备选 | 什么时候换 |
|---|---|---|---|
| 模型加载 / 量化 | unsloth `FastLanguageModel` | swift `quant_method` | 模型不在 unsloth 支持列表；国产卡 |
| 训练循环 | trl `SFTTrainer` | swift 自带 trainer | 要多模态、国产卡、一条命令跑完 |
| 显存优化 | unsloth kernel + trl 配置 | trl + Liger（融合算子，见 04_train.md §3.1）；swift 参数表 | 环境装不上 unsloth |

**显存优化这一层的"别重复叠"**：unsloth 的 `FastLanguageModel` 自带融合内核；
trl 自己不管 kernel，要额外 `pip install liger-kernel` + `use_liger_kernel=True`。
**两条路选一条**——用了 unsloth 就别再开 Liger（重复、可能冲突）；走纯 trl 才需要 Liger。
Liger 不是第四个训练框架，是插在 trl 上的插件，别和三个库并列当成"四选一"。
| 数据格式 | trl `Dataset Formats` 的定义 | unsloth / swift 各自 schema | 已用某库内置数据集 |
| 评测 | swift `评测` | 自建探针集 | 没有 evalscope 环境 |
| 部署 | unsloth `vLLM Deployment` | swift `推理和部署` | 要多后端（SGLang/LMDeploy）、国产卡 |
| 国产卡 | swift | 无 | — |

## 三条固定组合（可直接照抄）

### A. 最省显存的主流写法（默认推荐）

**unsloth 管加载、4bit、kernel、LoRA 挂载；trl 管 trainer、回调、checkpoint、日志。**

```python
from unsloth import FastLanguageModel
from trl import SFTConfig, SFTTrainer

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="<本地路径或 HF id>",
    max_seq_length=8192,
    load_in_4bit=True,
)
model = FastLanguageModel.get_peft_model(
    model, r=16, lora_alpha=16, lora_dropout=0.0, bias="none",
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                    "gate_proj", "up_proj", "down_proj"],
    use_gradient_checkpointing=True,
)
trainer = SFTTrainer(
    model=model,
    args=SFTConfig(output_dir="./out", max_length=8192),
    train_dataset=ds,
)
trainer.train()
```

要点：这样存出来仍是**标准 HF 权重 + adapter**，vLLM / transformers 直接能加载，不会被绑死在某个框架里。

### B. 一条命令试水（先验证数据和模板）

```bash
swift sft \
  --model <本地路径> \
  --tuner_backend unsloth \
  --tuner_type lora \
  --dataset <数据集名或 jsonl> \
  --max_length 4096
```

要点：先用它验证**数据格式、chat template、环境**三件事，跑通再决定要不要写代码。别把 swift 跑通当成训练脚本验收。

### C. 全参 / 多卡

- 全参放得下：trl + `SFTConfig`（unsloth 的全参支持有限，别硬上）
- 多卡分片：trl + DeepSpeed 配置；或 swift `--deepspeed zero2/zero3`
- 小模型多卡 + LoRA：unsloth DDP 最省事

## 综合判断四问（每个决策点都问一遍）

1. **有唯一解吗？** 有就锁死，不做无意义横评。
   国产卡 → swift；500k 级别 → unsloth；要最新算法（DPO/GRPO/蒸馏）→ trl。
2. **最简能跑通的是哪条？** 先用它验证数据和环境，别一上来写最复杂的。
3. **上限最高的是哪条？** 正式训练用这条；如果和最简不是同一条，方案里要写清升级路径。
4. **能组合吗？代价是什么？** 加载层与训练层可以分家（组合 A）；swift 是整包替代，不能和另外两个混在一个进程里。

## 组合禁忌（都是文档里写着的，不是猜的）

1. **swift + unsloth 后端**：`--target_modules all-linear` 会 `assert(type(target_modules) in (list, tuple))` 失败——unsloth 不展开模块名，必须写 `q_proj k_proj v_proj` 这类具体名（swift FAQ Q50）。
2. **不要在同一进程既 patch unsloth 全局、又走 swift 整包**。两个框架都改全局行为，报错极难定位。
3. **三库同名参数默认值不同**：
   - `learning_rate`：swift 全参默认 `1e-5`、LoRA 默认 `1e-4`（差 10 倍）
   - `lora_alpha`：swift 默认 32，trl 官方 unsloth 集成示例用 16
   - `max_length`：都指 token，但默认值各不相同
   照搬参数名不改值，等于静默改了训练方案。
4. **trl 的 vLLM 页不是部署文档**。它讲的是训练中用 vLLM 加速生成（GRPO 那类在线方法），部署看 unsloth 或 swift。

## 输出要求：方案里必须有三库对照表

写进 `02_training_plan.md`，形如：

| 决策点 | trl 的答案 | unsloth 的答案 | swift 的答案 | 采用 | 理由 |
|---|---|---|---|---|---|
| 显存不够 | `Reducing Memory Usage` | `500K Context Length` | 参数表（无专页） | unsloth + trl 配置 | 要 8k 且单卡，unsloth 4bit + 检查点实测省 40% |
| 数据格式 | `Dataset Formats` | `Datasets Guide` | `自定义数据集` | trl conversational | 中间格式，三库都能吃 |

再加两句：

- **为什么不用另外两条**（不是"没看"，是"看了不选"）
- **如果组合，哪层归谁**（如：加载归 unsloth，trainer 归 trl）

## 什么时候可以不看三库

只有一种情况：约束已经锁死唯一解（国产卡、环境里只有某个库、模型只被某个库支持）。

即使这样也要写一句"另外两个为什么不适用"——否则下次换机器又要从头评估一遍。

## 一条命令拿到全部三库对照

```bash
python scripts/docs_outline.py --matrix            # 19 个场景 × 三库覆盖 × 最简/最强
python scripts/docs_outline.py --recipes --step 3  # 某一步的详细版（含禁忌与验证）
python scripts/docs_outline.py --recipes --lib swift   # 只看 swift 那一列
```
