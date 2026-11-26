# 04 · 写脚本：先 smoke test，再全量

## 0. 顺序：先让它跑起来，再让它跑得好

**这一步最常见的失败不是写错参数，是根本没写出脚本。**

实测（`09_real_cases.md` 案例 16）：模型在这步花了 20 次调用、写了 5 个 `*_probe.py`
去验证 trl 的 chat template patch 行为，**最后 `train.sh` 一个字都没写**。
第 6 步一看没脚本，整个流程就断了。

正确的顺序：

```
1. 写出最小可跑脚本（参数全用保守值）          ← 必须完成，否则后面全废
2. 跑 20 步，看会不会 OOM / 报错               ← 失败就按第五节的开关顺序调一个
3. 跑通之后再谈优化（packing / batch / lr）     ← 前两步没完成，不许碰这个
```

**判断自己有没有卡住**：如果你正在写第 3 个 `*_probe.py`，停下来。
去写 `train.sh`，把不确定的地方写成注释和 TODO，跑起来再说。

**为什么**：`train.sh` 是这一步的**唯一产出物**。没有它，验证得再对也等于零。

## 原则

脚本不是"写出来就能跑"，是"**跑通了才算写出来**"。所以这一步分两半：

```
写脚本 → smoke test（20~50 步）→ 通过 → 才允许全量训练
                              └→ 不通过 → 改，不许硬跑
```

## 一、三库的最小骨架

**这三段不是三个互斥方案**：unsloth 那段本来就用 trl 的 `SFTTrainer`（官方组合方式，见 `08_cross_lib.md` 组合 A）；
swift 是整包替代，别和另外两个混在一个进程里。选哪条按 `02_decide.md` 的"综合判断四问"。

下面只是骨架，**字段名以你装的那个版本为准**。核对方法：

```bash
python scripts/docs_outline.py --recipes --step 5   # 三库各自的骨架页
python scripts/docs_outline.py --headings <上面给出的 md 路径>
```

打开后照抄它的参数名，不要背旧版本。

### trl（SFTTrainer）

```python
from datasets import load_dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer
from trl import SFTConfig, SFTTrainer

model_id = "models/Qwen__Qwen2.5-7B-Instruct"
model = AutoModelForCausalLM.from_pretrained(model_id, torch_dtype="bfloat16")
tokenizer = AutoTokenizer.from_pretrained(model_id)
dataset = load_dataset("json", data_files="data/train.jsonl", split="train")

lora = LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05,
                  target_modules="all-linear", task_type="CAUSAL_LM")

args = SFTConfig(
    output_dir="outputs",
    num_train_epochs=2,
    per_device_train_batch_size=2,
    gradient_accumulation_steps=8,
    learning_rate=2e-4,
    max_length=4096,              # 旧版本叫 max_seq_length
    packing=True,
    gradient_checkpointing=True,
    bf16=True,
    logging_steps=10,
    save_steps=200,
    eval_strategy="steps",        # 旧版本叫 evaluation_strategy
)

trainer = SFTTrainer(model=model, args=args, train_dataset=dataset,
                     peft_config=lora, processing_class=tokenizer)
trainer.train()
trainer.save_model("outputs/final")
```

**怎么找**：这是 **A 类（哪些参数名合法）**，按 06 第二层的规则 → 找**入口**类目录
（`install` / `get-started` / `入门`）里的"最小可跑示例"。
**别去配方类目录拼参数名**——配方给的是"怎么达成目标"，参数名只是顺带出现，容易抄到旧版。

```bash
python scripts/docs_outline.py --dirs --type 入口        # 146 tok：先看入口目录在哪
python scripts/docs_outline.py --search quickstart --lib trl
```

**结果（快照）**：trl `Quickstart`。参数名以它为准，不要背旧版本的
（`max_seq_length` → `max_length`、`evaluation_strategy` → `eval_strategy` 都改过）。

### unsloth（FastLanguageModel）

```python
from unsloth import FastLanguageModel
from trl import SFTConfig, SFTTrainer

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="models/Qwen__Qwen2.5-7B-Instruct",
    max_seq_length=4096,
    load_in_4bit=False,          # QLoRA 时改 True
    dtype=None,
)
model = FastLanguageModel.get_peft_model(
    model, r=16, lora_alpha=32,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                    "gate_proj", "up_proj", "down_proj"],
    use_gradient_checkpointing="unsloth",
)
# 之后接 trl 的 SFTTrainer / SFTConfig，参数同上
```

**怎么找**：要"从零怎么写"是 **A 类** → 入口目录；要"4bit 能压到什么程度"是 **C 类** → 实证目录。
**同一个库，这两类几乎从不放在同一个目录**，所以先 `--dirs` 分开定位，别在一篇里找全部。

```bash
python scripts/docs_outline.py --dirs --type 入口      # 入门写法
python scripts/docs_outline.py --dirs --type 实证      # 极限实操
python scripts/docs_outline.py --search beginners --lib unsloth   # 入门路径（第 1 条就是）
python scripts/docs_outline.py --search 500k --lib unsloth         # 4bit 极限（唯一命中）
```

**结果（快照）**：`Fine-tuning for Beginners`（从零）；QLoRA 的 4bit 加载看
`500K Context Length Fine-tuning`（它给的 `load_in_4bit=True` + 检查点组合是实测过的）。

### ms-swift（命令行）

```bash
swift sft \
  --model models/Qwen__Qwen2.5-7B-Instruct \
  --tuner_type lora \
  --tuner_backend peft \
  --dataset data/train.jsonl \
  --num_train_epochs 2 \
  --per_device_train_batch_size 2 \
  --gradient_accumulation_steps 8 \
  --learning_rate 1e-4 \
  --lora_rank 8 \
  --lora_alpha 32 \
  --max_length 4096 \
  --output_dir outputs
```

注意两个容易写错的地方（本仓实测）：
- 参数是 **`--tuner_type`**，不是 `--train_type`（后者在 swift 文档里 0 次命中）
- LoRA 默认学习率 **1e-4**，全参默认 1e-5；换 `tuner_type` 不改 lr 会训坏

想直接借 unsloth 的内核省显存：把 `--tuner_backend` 改成 `unsloth` 即可（默认 `peft`）。
此时 `--target_modules` 不能写 `all-linear`，必须写具体模块名（swift FAQ Q50）。

**怎么找**：要"开关叫什么、取什么值"是 **A 类** → 找**手册**类目录。
手册类有两种形态，**先看它是一页还是一章**：

```bash
python scripts/docs_outline.py --dirs --type 手册        # 54 tok：手册目录在哪
#  → [swift] Instruction/  39 —— 39 页挤在一个目录，说明它不按专题拆，而是收成了几本"手册"
python scripts/docs_outline.py --headings docs/swift/Instruction/Command-line-parameters.md
#  → 一本总表：### Tuner参数 (316) → #### 全参 (329) / #### LoRA (337) / #### UNSLOTH (404)
#              ### 量化参数 (162) / ### 数据参数 (59)
grep -n -E -- "--(tuner_type|quant_bits|lora_rank|target_modules)\b" \
  docs/swift/Instruction/Command-line-parameters.md
```

这一步的判据是"**手册里骨架到节、grep 到行**"，因为手册的节内条目太多，标题不再给信息。

**结果（快照）**：`命令行参数`（中文总表）；jsonl schema 看 `Customization/自定义数据集`；
`--tuner_type` 的取值在 **`### Tuner参数`** 下（`#### 全参` / `#### LoRA`），**不在**「基本参数」节。

## 二、参数怎么定（不是抄，是按判据调）

| 参数 | 起点 | 调整判据 |
|---|---|---|
| `learning_rate` | LoRA 2e-4 / 全参 2e-5 | loss 不降 → 加大；震荡/发散 → 减小 |
| `num_train_epochs` | 2~3 | eval loss 开始回升 = 过拟合，减 |
| `per_device_train_batch_size` | 显存能放下的最大值 | OOM → 降，再用 GA 补 |
| `gradient_accumulation_steps` | 8~16 | 保证 effective batch ≥ 32 |
| `max_length` | 数据 p99（向上取整到 1024） | OOM → 降长度或开 ckpt |
| `packing` | 数据短且多 → 开 | 长度差异极大 → 关，避免跨样本污染 |
| `gradient_checkpointing` | 默认开 | 换显存，代价 20~30% 速度 |
| `warmup_ratio` | 0.03 | 大 lr 时可加到 0.05 |
| `save_steps` | 200 | 至少留 2~3 个检查点做对比 |
| `eval_strategy` | steps，每 100~200 步 | 没有 eval 就无法判断过拟合 |

### 2.1 带 thinking 的模型（Qwen3 / Qwen3.5 等）：训练和推理必须同一套开关

**这是最容易漏、现象又最迷惑的一项。** Qwen3 系列的 chat template 有 `enable_thinking` 参数，
它决定 prompt 里**是否预填** `<think></think>`：

| 设置 | 模板行为 | 模型学到什么 |
|---|---|---|
| `enable_thinking=False` | prompt 末尾**预填** `<think>\n\n</think>\n\n`，且这段不算 loss | 直接学回复正文 |
| `enable_thinking=True`（默认） | prompt 不预填，模型要自己先写 think 再写正文 | 但你的数据里没有 think 标签 → 它补个**空的** |

**训练和推理必须一致。** 不一致的典型症状：

```
输出：<think>

</think>

感谢您的来信。关于订单A3482...
```

空 think 不影响内容，但格式脏、白耗 token；如果训练时真教了 think 内容而推理关了，
则**思考被截断，答案质量掉一档**。

**规矩**：

```python
# 数据里没有 think 标签（普通 SFT）→ 训练和推理都关
tokenizer.apply_chat_template(msgs, enable_thinking=False, ...)
# trl: SFTConfig(..., chat_template_kwargs={"enable_thinking": False})
# 推理: 同样传 enable_thinking=False（vLLM 用 chat_template_kwargs）

# 数据里**有** think 标签（推理蒸馏）→ 两边都开，且要确认
# generation mask 覆盖到 think 段，否则 think 不参与 loss
```

依据：trl `01_conceptual_guides/chat_templates.md:94,115`——它明确写了
`enable_thinking=False` 时"思考块由 prompt 预填、留在 generation 标记之外"。

**实测**（Qwen3-0.6B，80 条无 think 标签的数据，训练时没设该参数）：
推理输出带空的 `<think></think>` 前缀，内容正确但格式不干净。

**怎么快速判断你踩没踩**：生成一条，看开头有没有 `<think>`。
有 → 训练和推理的 `enable_thinking` 不一致（或都没设）。

## 三、显存不够时的开关顺序（从便宜到贵）

按这个顺序试，**一次只动一个**，动了就重测：

1. 降 `per_device_train_batch_size`（几乎无损，只是慢）
2. 开 `gradient_checkpointing`（省 30~50% 激活，慢 20~30%）
3. **开 `use_liger_kernel=True`**（一行，融合算子省显存，见 3.1）
4. 降 `max_length`（直接砍上下文，看数据 p99 决定能降到哪）
5. 关 `packing`（packing 会让长序列叠加，峰值更高）
6. LoRA → QLoRA（省一半以上，精度略降）
7. 多卡 / ZeRO（改并行策略，最复杂，最后才上）

### 3.1 Liger-Kernel：一行代码省显存的融合算子

**它是什么**：LinkedIn 开源的一组 Triton 融合算子，把 RMSNorm / RoPE / SwiGLU /
CrossEntropy / **FusedLinearCrossEntropy** 换成功能等价但更省显存的实现。
省显存的大头是 FusedLinearCrossEntropy——标准做法要把 `[batch×seq, vocab]`
的 logits 整个物化（词表 15 万时这是显存杀手），融合算子把 linear + CE 合成一步，
不落地这个巨大中间张量。官方数据：显存降约 60%、吞吐升约 20%、上下文可放大约 4 倍。

**它不是训练框架，是插件**：包在 TRL / LLaMA-Factory / Axolotl 之上用。
本 skill 走 trl，而 **trl 原生支持**，所以接入只要两步：

```bash
pip install liger-kernel          # 需 torch ≥2.1.2、triton ≥2.3.0（CUDA）
```

```python
from trl import SFTConfig
# 训练配置里加一个参数即可（config.yaml 里对应 train.use_liger_kernel: true）
args = SFTConfig(..., use_liger_kernel=True)
```

**你的模板已经留好开关**：`config.yaml` 里的 `train.use_liger_kernel`，
`train.py` 会把它传给 `SFTConfig`。默认 `false`；显存紧时改 `true` 重跑 smoke test。

**什么时候不要开**：
- 模型架构不在 Liger 支持列表（覆盖 Llama/Mistral/Gemma/Qwen 等 30+ 主流架构，
  小众架构无效，开了也没用甚至报错）
- 已经在用 unsloth：`FastLanguageModel` 自带一套融合内核，再叠 Liger 是重复，
  收益很小还可能冲突（见 `08_cross_lib.md`）
- AMD/昇腾卡：支持但收益和踩坑情况要单独验证，本 skill 只在 NVIDIA CUDA 上实测过

**验证收益的正确姿势**：开之前先记下 smoke test 的峰值显存（模板会打
`SMOKE_REPORT_JSON` 里的 `peak_vram_mib`），开之后**同样 step 数**再跑一次，
比数字。**不要信"降 60%"的宣传数字**——那是官方特定配置下的，你的 batch/seq/词表不同。

**怎么找到它的文档（心法，比记住参数重要）**：
Liger 没有独立页被同步进来，但参数就在本地 trl 文档里：

```bash
grep -rn "use_liger_kernel" docs/trl --include=*.md | head
# → sft_trainer.md / dpo_trainer.md / ... 10 个 trainer 都出现
python scripts/docs_outline.py --headings docs/trl/02_trainers/sft_trainer.md
# → SFTConfig 参数签名里有 use_liger_kernel / liger_kernel_config
```

官网那篇专门的整合说明页叫 `liger_kernel_integration`（trl docs 内）。
**官网改结构后，照样 grep 参数名找得到——这就是心法对抗文档变化的样子。**

## 四、smoke test 协议（不许跳）

目的：用 2 分钟证明"这个配置跑得动"，而不是跑 8 小时后发现 OOM。

```bash
# 只跑 30 步，其它参数和真实训练完全一致
# 30 步是下限，不是随便定的：1~2 步会把一次性开销（编译/预热）摊进来，
# 实测 1 步测速比 20 步高估 320%（6.7 小时被估成 28 小时），见 09 案例 12
python train.py --max_steps 30 --logging_steps 1 --save_steps 1000
```

跑之前，在脚本里加一段峰值显存记录 + 一次生成检查：

```python
import torch
# ... trainer.train() 之后
peak = torch.cuda.max_memory_allocated() / 1024**3
print(f"PEAK_VRAM_GIB={peak:.2f}")

trainer.model.eval()          # 必须：train 模式下 generate 可能输出乱码（见 09 案例 10）
print(generate_one(trainer.model, tokenizer, probe_sample))
```

通过的五个条件：

| 条件 | 判据 |
|---|---|
| 不 OOM | 全程没抛 `CUDA out of memory` |
| 显存有余量 | 峰值 ≤ 可用 × 0.90 |
| loss 在动 | 30 步内 loss 有下降趋势（不是平的，也不是 NaN） |
| **速度达标** | 用 `train_runtime / 步数` 算 s/step（**丢掉前 2 步**），换算的全程时长 ≤ 你定的上限 |
| **生成能看** | `eval()` 后生成 1 条，格式和内容对得上（不是 `////` 或重复） |

**速度那一栏怎么算**（这一步做错，后面所有成本判断都是错的）：

```
s/step = runtime_metrics['train_runtime'] / 总步数       # 不要用自己掐的秒表，口径会混
全程时长 = s/step × (总样本数 / (bs×grad_accum)) × epochs / 3600
判据     = 全程时长 ≤ 第 1 步定的速度线
```

完整方法见 `10_measure.md` 第一、二节。**顺序不能反：先测准，再判断能不能接受。**

最后两条最容易被跳过：速度不测就没法比方案，生成为乱码则说明评测代码错了（案例 10）。
**smoke test 五项全过，才允许开全量。**

## 五、常见错误对照

| 报错/现象 | 大概率原因 | 处理 |
|---|---|---|
| `CUDA out of memory` | batch/seq 太大，或没开 ckpt | 按开关顺序降 |
| loss 一直是 NaN | lr 太大 / bf16 不支持 / 数据有脏样本 | 降 lr，查数据 |
| loss 完全不降 | label mask 全被 mask 掉 / lr 太小 / 数据格式错 | 打印一条 tokenized 样本看 labels |
| 训练极慢 | 没开 FlashAttention / packing 没生效 | 查库文档的性能章节 |
| 保存的 adapter 加载报错 | base 路径不一致 / target_modules 不匹配 | 用训练时的同一 base |
| eval loss 先降后升 | 过拟合 | 减 epoch，加数据，加 dropout |

## 六、产出物

- `train.py`：训练主体（smoke / full **共用这一份**，别复制成两份代码）
- `train.sh`：启动命令（smoke / full 两模式，参数不要散落在多处）
- `config.yaml`：超参（和代码分开，做对照实验时只改这个）
- `outputs/`：检查点 + `trainer_state.json` + 日志
- 在 `02_training_plan.md` 里补**三个数**（只补显存是不够的）：
  - smoke test 实测峰值显存 = ___ GiB
  - 实测 s/step = ___（`train_runtime / 步数`，丢前 2 步）
  - 推算全程时长 = ___ 小时 / ___ GPU-hours
  - 三个数都要能和 `10_measure.md` 第三节的基线表对接

## 七、把脚本交付给用户（别漏这一步）

**脚本写到磁盘上，不等于交付给了用户。** 这是实测踩出来的：
e2e2 那一轮 `train.sh`、`train.py`、`outputs/` 全都在磁盘上，loss 也跑通了，
但 agent 的回复是空的超时错误——**用户从头到尾没看到脚本长什么样**。
报告里只写"已生成训练脚本"也不算，用户拿不到、改不了。

**规矩：第 5 步结束，必须把下面三样完整交给用户：**

1. **三件套全文**：`train.py`、`train.sh`、`config.yaml` 的完整内容（不是"已生成"三个字，也不是路径）——
   用户要能直接复制走、改自己的路径和参数。
2. **怎么跑**：明确写出 smoke 和 full 两条命令，以及训练环境的 python 路径。
3. **改哪里**：用三句话点出用户必须按自己场景改的字段（模型路径、数据路径、
   显存/时长上限），并提示 thinking 开关（§2.1）——这是最容易复现的坑。

**从 `templates/` 起步**：`templates/train.py` + `train.sh` + `config.example.yaml`
是实测跑通的版本泛化来的，复制后按上面第 3 条改即可。但记住红线：
**给脚本之前先讲清它做了什么、哪些是精华、哪些要按业务改**——
照抄能跑不等于理解了；用户改对了才算交付完成。

**给 AI 执行者的自检**：收尾时问自己一句——
"如果我现在把对话记录给用户，他能不能直接拿到一份可运行、知道自己该改哪的脚本？"
答不出"能"，就是没交付。
