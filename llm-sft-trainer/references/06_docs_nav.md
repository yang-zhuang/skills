# 06 · 查文档：心法四层

## 目的

**这篇不给你答案，给你"怎么得到答案"。**

之前这里是「问题 → 该看哪篇」的表。那是**结果**，不是方法。
结果是会烂的：官网改组章节、改页面标题、改参数名，表就全错，
而你还以为自己在按图索骥（实测过一次：transformers 一次改组迁移了 25 页）。

心法只依赖两样东西：

- **文档的形态特征**——章节怎么切、标题怎么措辞、正文里有没有命令行参数
- **证据的分级**——谁说的话可信到什么程度

这两样比路径稳得多。所以顺序是：**先有方法 → 再跑出结果 → 结果当缓存，过期重跑**。
`references/recipes.json` 那张三库对照表就是一次跑出来的缓存，不是真理。

---

## 第一层 · 定性：你要的是哪一种"知道"

任何训练问题的答案，一定是下面四类之一。**先定性再动手**，否则会在错误的文档形态里空转。

| 类型 | 你问的是 | 答案长什么样 | 能回答它的文档 | 判断标志 |
|---|---|---|---|---|
| **A 有没有** | 支不支持 QLoRA？能不能 packing？ | 一个开关名 / 一个函数名 | 参数表、API 索引 | 有 `--xxx` 或 `xxx=` 的行 |
| **B 怎么配** | 显存不够调哪几个？长上下文怎么配？ | 一组参数 + 为什么这么配 | how-to、guide、best practice | 标题是动名词：`Reducing...` `How to...` `最佳实践` |
| **C 值多少** | 能压到多少 G？能跑多长？快几倍？ | 某个配置下的实测数字 | blog、example、FAQ | 标题带数字/模型名，正文有 GB、tok/s、x 倍 |
| **D 怎么搭** | trl 能不能配 unsloth？swift 能用 unsloth 当后端吗？ | 一句「A 的某参数可以指向 B」 | **跨库引用**（一个库名出现在另一个库的文档里） | `grep -rn "<另一个库名>" docs/<库>` 有命中 |

**D 类单独说**，因为它不能用前三类的办法找——答案不在任何一篇页面里，
在**两个库的接口处**。判断标准只有一条：**必须看到对方库的名字出现在文档里**，
不能靠「它们都是训 LoRA 的，应该能搭」这种推理。

```bash
grep -rn "unsloth" docs/trl --include=*.md | head        # → 有 unsloth_integration 页
grep -rn "from trl import" docs/unsloth --include=*.md   # → 官方示例直接用 trl
grep -rn "tuner_backend" docs/swift --include=*.md       # → 能指定 unsloth 当后端
```

三条都命中，才敢说"能组合"。命不中就是不能搭，别硬试。

**反例（我自己犯的）**：想知道「显存能压到多少」（C 类），去翻 trl 的
`Reducing Memory Usage`（B 类页面）。那篇给的是 10 个开关的清单，**一个数字都没有**。
不是那篇没用，是**拿 B 类文档回答了 C 类问题**。

**为什么抗变化**：分类依据是「答案的形态」，不是文档放在哪。库怎么改名都不影响。

---

## 第二层 · 认骨架：目录名的功能词告诉你它装什么

**先给规则，不给库名。** 任何文档站，目录名都是功能词堆出来的；功能词只有那么几十个。

| 目录名里出现这些词（英/中） | 目录类型 | 里面装什么 | 对应哪类问题 |
|---|---|---|---|
| concept / architecture / 原理 / 概念 / 架构 | 概念 | 为什么成立 | 判断可行性 |
| how-to / guide / tutorial / recipe / practice / 指南 / 教程 / 最佳实践 / 流程 | **配方** | 一个目标一篇，给一组参数 | **B 怎么配** |
| blog / case / example / benchmark / 案例 / 实战 / 战报 | **实证** | 带数字的战报 | **C 值多少** |
| reference / api / command / cli / config / 参数 / 手册 / instruction | **手册** | 开关清单，查而不读 | **A 有没有** |
| trainer / algorithm / method / 算法 / GRPO / DPO / RL | 算法 | 一个算法一篇 | 选算法 |
| model / 模型 | 对象 | 按模型组织 | 「这个模型怎么训」 |
| install / get-started / 安装 / 入门 / basic | 入口 | 最小可跑 | 第一次跑通 |
| integration / plugin / 集成 / connection | 集成 | 和外部工具怎么搭 | **D 怎么搭** |
| inference / deploy / serving / 推理 / 部署 / export | 部署 | 训练完怎么上线 | 验收 |
| troubleshoot / faq / 常见问题 / 答疑 | 答疑 | 报错对照表 | 卡住时 |

命令已经把这张表实现了（`docs_outline.py` 里的 `DIR_WORDS`）：

```bash
python scripts/docs_outline.py --dirs                 # 每个目录带类型标注，全表约 700 tok
python scripts/docs_outline.py --dirs --type 配方      # 只要配方类，219 tok
python scripts/docs_outline.py --dirs --type 实证      # 只要实证类，44 tok
```

**规则里没有一个库的名字**，所以官网把 `blog` 改成 `case-studies`、把 `how_to_guides`
改成 `guides`，规则照样命中。这是它和「结果表」的根本区别。

### 把规则跑在三库上（这是应用，不是规则本身）

```
=== trl（57 页 / 10 个目录）===
  trl/                       57  —
    00_getting_started/       4  入口
    01_conceptual_guides/     3  概念
    02_trainers/              8  算法
    03_how_to_guides/        10  配方
    04_integrations/         10  集成
    05_examples/              3  实证
    06_api/                   5  手册
    07_experimental/         14  实验

=== unsloth（202 页 / 34 个目录）===
  basics/                                43  入口
    inference-and-deployment/            14  部署
      vllm-guide/                         2  配方
  blog/                                  13  实证
  get-started/                           35  入口
    fine-tuning-llms-guide/               4  配方
    reinforcement-learning-rl-guide/     11  配方
  models/                                86  对象
    tutorials/                           37  配方
  integrations/                          14  集成

=== swift（75 页 / 10 个目录）===
  Instruction/               39  手册
  BestPractices/             20  配方
  Customization/              3  定制
  GetStarted/                 3  入口
  Megatron-SWIFT/             9  —
```

由这张表能推出三件事（**推导结果，不是背下来的结论**）：

1. **trl 的配方集中在一个目录**（`03_how_to_guides` 10 页）→ 它把"问题"当独立一章做 → 强项是**覆盖全**。
2. **unsloth 的配方散在 10 个目录**（`fine-tuning-llms-guide`、`tutorials`、各模型子目录）→ 按场景/模型组织 → 强项是**具体模型的具体做法**。
3. **swift 没有实证目录**（`--type 实证` 只命中 trl 和 unsloth）→ 这是真空不是遗漏 → **要数字只能去别的库找**；但 `Instruction` 是手册类，**A 类问题（有没有）查它最快**。

### 判读会错，抽一页验证

目录类型是**启发式**，会错。花 100 tok 确认：

```bash
python scripts/docs_outline.py --toc --in 03_how_to_guides
#  标题是"一个问题一篇" → 判读成立
#  全是 SFT/DPO/GRPO 这种算法名 → 判错了，回去看 --dirs 全表
```

**实测局限（别当万能）**：我试过用"标题词性"（动名词占比、带数字占比）自动判类型，
结果**对中文库完全失效**——swift 全库动名词 0%、带数字 0%，因为中文标题不写 `-ing`。
所以：**目录名的功能词可用（中英都覆盖），标题措辞只能当辅助**。

---

## 第三层 · 渐进读：文件夹 → 页 → 节 → 正文块

**人类看文档不是"打开一篇读到底"，是逐层收窄。** 四层阶梯，每层都先看代价再决定要不要展开。

| 层 | 看什么 | 命令 | 实测代价（三库全量） |
|---|---|---|---|
| **L0 文件夹结构** | 有哪些目录、各装几页（**页数含子目录**） | `--dirs` | **57 行，约 720 tok** |
| **L1 页面清单** | 某目录下有哪些页 | `--toc --in <目录>` 或 `--lib X --depth 2` | 全量 358 行 ≈ 7.0k；单库 874 tok |
| **L2 页内标题** | 这页分几节 | `--headings <页>` | 全量到 `##` 19.6k / `###` 43k / `####` 58k |
| **L3 正文** | 具体段落 | `--read <页>` | 全量 **1.59M tok**，必须分块 |

**关键判断：文件夹结构便宜到可以无脑一次给全（约 720 tok），页内标题层级才贵（到 `####` 是 58k），正文绝对不能整库读（1.59M）。**

所以默认动作是：`--dirs` 一次拿全 → 挑 1~2 个目录 → `--toc --in` 看页 → `--headings` 看节 → `--read` 读正文块。
**不要一上来就 `--toc --level h4`**：58k tok 换来的只是"所有页的所有小节标题"，而你真正需要的只有 2~3 页。

### L3 的规则：正文按 10k 分块，小页一次读完

这是硬性操作规则，不是建议：

| 情况 | 动作 |
|---|---|
| 整页 ≤ 10k tok | **一次读完**，别分块（分块反而丢上下文） |
| 整页 > 10k tok | 读第 1 块 → 判断够不够 → 不够再读第 2 块，**不要一次要全部** |

`--read` 已经按这个规则实现了：

```bash
python scripts/docs_outline.py --read docs/swift/Instruction/Command-line-parameters.md
#  985 行 ≈ 30,344 tok | 预算 10,000/块 | 共 4 块
#  [第 1/4 块] 行 1-236 ≈ 8,689 tok
#  ...
#  下一块：--read ... --chunk 2

python scripts/docs_outline.py --read docs/trl/03_how_to_guides/use_model.md
#  60 行 ≈ 608 tok | 预算 10,000 → 整篇一次读完
```

切点会回退到空行或标题行，不会把句子劈开。`--budget 3000` 可以改预算。

**实测参考**：334 页里 98 页超过 20k 字符；最长的 `swift/Supported-models-and-datasets.md`
是 83k tok（5 个标题、一张巨表），这种页面**绝对不要整篇读**，先 `--headings` 看它只有 5 节，
再用 `grep -n` 直接定位模型名。

### 页 → 节 → 行：骨架定节，grep 定行

**骨架回答「去哪一节」，grep 回答「哪一行」。** 两级都要，别跳。

| 层级 | 手段 | 能到多细 | 什么时候停 |
|---|---|---|---|
| 页 → 节 | `--headings`（打到 `####`，4 级） | 能定位到「Tuner参数 / 量化参数 / Gradient checkpointing」这一级 | 节内只有几段 → 直接读节 |
| 节 → 行 | `grep -n` | 精确到行号和参数名 | 节内有几十条参数/几十段 → 必须 grep |

**什么情况可以直接读节**：`##` 在 5~15 个，每节几十行，一节就是一个独立概念。
**什么情况必须再 grep**：某一节内部还有几十个条目（典型是参数表），标题不会告诉你哪个参数在哪行。

**真实对照（同一天实测）**：

```
# 一节一个开关：trl 省显存页 —— 10 个 ##，读骨架就能挑节
     1  # Reducing Memory Usage
    11    ## Truncation
    52    ## Packing
    90    ## PEFT for parameter-efficient fine-tuning
   116    ## Padding-free
   137    ## Activation offloading
   171    ## PyTorch caching allocator
   227    ## Gradient checkpointing

# 一节一招：unsloth 500K 页 —— 3 个 ###，读骨架就知道它用了哪三招
    23      ### 📐 Unsloth Loss Refactoring: Chunk & Fuse
    40      ### 🏁 Unsloth Gradient Checkpointing Enhancements
    78      ### 🔓 Tiled MLP: Unlocking 500K+

# 节内还有几十条：swift 命令行参数 —— ## 只有 5 个大区，但 ### 层是可用的
    10  ## 基本参数
    34    ### 模型参数   59 ### 数据参数   103 ### 模板参数
   162    ### 量化参数  316 ### Tuner参数  445 ### vLLM参数
   505  ## 集成参数    845 ## 特定模型参数   964 ## 其他环境变量
```

swift 那张表**读 `--headings` 是有用的**——它能告诉你去 `### 量化参数`（162 行）或
`### Tuner参数 → #### LoRA`（337 行）。但到了那节内部，几十个参数混在一起，
标题不再给你信息，**这时才必须按参数名 grep**：

```bash
python scripts/docs_outline.py --headings docs/swift/Instruction/Command-line-parameters.md
#  → 先收敛到 ### 量化参数 / ### Tuner参数 → #### LoRA

grep -n -E -- "--(tuner_type|quant_bits|gradient_checkpointing|packing)\b" \
  docs/swift/Instruction/Command-line-parameters.md
#  → 再精确到行：--tuner_type 在 410，--quant_bits 在 411
```

**所以"骨架太粗"不是跳过骨架，是骨架只到节、不到行。** 跳过骨架直接 grep 全文件，
在 1000 行里搜一个词会命中一堆无关位置（`packing` 在这张表里 9 处）。

### 辅助判断：标题的措辞（只对英文可靠）

进到页这一层，标题措辞还能再帮你一次。**但这只是辅助，不能当规则用**——实测对中文库失效。

| 措辞 | 页面类型 | 它给你什么 | 真实例子 |
|---|---|---|---|
| 名词短语 | **对象页** | 这个对象的写法 / 它存在 | `SFT`、`DeepSpeed`、`PEFT`、`命令行参数` |
| 动名词 / 动词短语 | **问题页** | 解决某问题的**配方**（一组参数 + 理由） | `Reducing Memory Usage`、`Speeding Up Training`、`Distributing Training` |
| 带数字 / 模型名 / 极限词 | **实证页** | **那个配置**下做到的**数字** | `Training Beyond 1M Tokens`、`500K Context Length`、`3x Faster ... Packing` |

中文文档不这么写标题（swift 那批是「快速训练VL模型」「多模态GRPO完整实验流程」），
所以中文要走**词义**：`快速/如何/指南/流程/最佳实践` → 配方；`实测/战报/跑通` → 实证。

**三种页面不能互相替代**，这是最容易犯的错：

- 只有对象页 → 知道开关存在，不知道怎么组合 ❌
- 只有问题页 → 知道配方，不知道能压到多少 ❌
- 只有实证页 → 知道别人做到了，不知道怎么在自己模型上复现 ❌

### 两种搜索，分工不同

| 手段 | 搜什么 | 什么时候用 | 命令 |
|---|---|---|---|
| `--search` | **元数据**（标题 / 路径 / 章节名） | 找**页**：不知道翻哪篇 | `docs_outline.py --search memory --lib trl --in how_to_guides` |
| `grep -rn` | **正文**（每一行） | 找**节 / 参数**：已定位到页，找具体段落 | `grep -n -i "gradient checkpointing" <file>` |

`--search` 只索引标题，**搜不到参数名**（参数名在正文里）。
`grep -rn` 在 334 页里搜一个词会命中几十处，**噪音盖住信号**。
所以：先 `--search` 缩到 1~3 页，再 `grep` 定到节。`--in` 可以先把范围锁在某一章。

---

## 第四层 · 证据分级：这段话能信几分

同一个问题，三类文档给三种强度的话。混着信就会翻车。

| 来源 | 它**保证**什么 | 它**不保证**什么 | 强度 |
|---|---|---|---|
| 参数表 / API 里有一行 | 这个开关**存在** | 有效、对你的模型有效、能和别的共存 | ★★ |
| 问题页说「推荐这样做」 | 作者的**经验配方** | 在你的卡上成立、当前版本还是这样 | ★★★ |
| 实证页给出数字 | 在**那个模型 + 那个卡 + 那个版本**下有效 | 换条件还成立 | ★★★★ |
| **本机 smoke test** | 在你的环境里成立 | 换个 batch size 还成立 | ★★★★★ |

结论链永远是：

```
参数表给候选  →  问题页给配方  →  实证页给预期  →  本机 smoke test 给结论
   有什么          怎么配           能到多少           我这能不能行
```

**前三个都是「读来的」，只有最后一个算「知道的」。**
`04_train.md` 要求 smoke test 五项全过才开全量，就是为了补上最后这一格。

---

## 兜底：三层退让

| 情况 | 说明什么 | 退到哪 |
|---|---|---|
| **问题页没有** | 这个库不把它当独立问题 | 去**对象页的组合**里找。例：swift 没有「省显存」专页，因为它 = `--tuner_type` + `--quant_bits` + `--gradient_checkpointing` 的组合 |
| **参数表没有** | 能力不原生支持，或要外挂 | 去**集成**类目录（`integration` / `plugin` / `集成`）。例：trl 的 DeepSpeed 不在参数表，而在 `04_integrations/deepspeed_integration.md` |
| **三库都没有** | 这是**真空地带** | 自己做实验，结论写回 `09_real_cases.md`。不许拿「文档没写」当「做不到」 |

---

## 完整演绎：一个真实问题走完四层

**问题**：「显存不够，想把显存压下来。」

### 第 1 层 · 定性

拆成两问：**怎么配**（B）+ **能压到多少**（C）。B 和 C 都要——
这一步就决定了「三库都要看」不是客套。

### 第 2 层 · 认骨架 → 定目录

问题「显存不够」= **B 类（怎么配）+ C 类（能到多少）**，所以只需要两类目录：配方 + 实证。
（这个判断来自第一层的定性，不是来自"我知道哪篇"。）

```bash
python scripts/docs_outline.py --dirs --type 配方     # 219 tok
python scripts/docs_outline.py --dirs --type 实证     # 44 tok
```

真实输出：

```
=== trl（57 页 / 10 个目录，只看「配方」）===
    03_how_to_guides/    10  配方

=== unsloth（202 页 / 34 个目录，只看「配方」）===
      fine-tuning-llms-guide/                 4  配方
      tutorials/                             37  配方
      ...（共 10 条）

=== swift（75 页 / 10 个目录，只看「配方」）===
    BestPractices/         20  配方

=== trl（...只看「实证」）===
    05_examples/     3  实证

=== unsloth（...只看「实证」）===
    blog/    13  实证
```

**swift 在「实证」那一步没有输出**——它没有实证类目录。这是读出来的事实，不是猜测。

候选目录就这几行，**整个过程没读一行正文**。

### 第 3 层 · 渐进读：页 → 节 → 正文块

#### 3.1 先看目录下有哪些页（`--toc --in`）

```bash
python scripts/docs_outline.py --toc --lib trl --in how_to_guides
#  →  Reducing Memory Usage / Speeding Up Training / Distributing Training /
#     Training Beyond 1M Tokens / Using Trained Models ...
#     10 篇里 6 篇是动名词 → 整章是「问题导向」，判断成立

python scripts/docs_outline.py --toc --lib unsloth --in blog
#  →  3x Faster ... Packing / 500K Context Length / QAT ...
#     13 篇里 3 篇带数字 → 实证章，判断成立
```

#### 3.2 再看选中页的标题骨架（`--headings`）

**trl 那篇骨架够细，读骨架就能挑节：**

```bash
python scripts/docs_outline.py --headings docs/trl/03_how_to_guides/reducing_memory_usage.md
#  →  10 个 ##，每个是一个开关：Truncation / Packing / PEFT / Padding-free /
#     Activation offloading / PyTorch caching allocator / Gradient checkpointing ...
```

**结论**：一张**完整的开关清单**。价值在「全」，不在「深」。
**这页只有 3,121 tok，所以下一步直接整篇读，不用分块。**

#### 3.3 读正文：小页整篇，大页分块

```bash
python scripts/docs_outline.py --read docs/trl/03_how_to_guides/reducing_memory_usage.md
#  255 行 ≈ 3,121 tok | 预算 10,000 → 整篇一次读完      ← 便宜，一次吃掉
```

```bash
python scripts/docs_outline.py --read docs/swift/Instruction/Command-line-parameters.md
#  985 行 ≈ 30,344 tok | 预算 10,000/块 | 共 4 块
#  [第 1/4 块] 行 1-236 ≈ 8,689 tok                    ← 贵，先只读第 1 块
#  下一块：--read ... --chunk 2
```

**这就是"按预算读"的实际手感**：同样是"看一篇文档"，一篇 3k 一次吃完，
一篇 30k 先吃 1/4，读完再判断要不要继续。**不要因为"文档才 30k"就整篇拉进来。**


**unsloth**：骨架够细，但它给的不是清单而是**招式**。

```bash
python scripts/docs_outline.py --headings docs/unsloth/blog/500k-context-length-fine-tuning.md
#  →  Loss Refactoring: Chunk & Fuse / Gradient Checkpointing Enhancements / Tiled MLP
```

**结论**：三招组合把上下文推到 500K。价值在「上限参照」，但**只对那个模型和卡成立**。

**swift**：骨架只到「哪一类参数」，具体哪个参数要 grep。

```bash
python scripts/docs_outline.py --headings docs/swift/Instruction/Command-line-parameters.md
#  →  ### 量化参数 (162) / ### Tuner参数 (316) → #### LoRA (337)
#     骨架到此为止：知道去「量化参数」或「Tuner参数 → LoRA」，但不知道具体参数名

grep -n -E -- "--(tuner_type|quant_bits|gradient_checkpointing|packing|max_length)\b" \
  docs/swift/Instruction/Command-line-parameters.md
#  →  410: --tuner_type full/lora
#     411: --quant_bits 4
```

**结论**：`--tuner_type` 一个参数切档、`--quant_bits 4` 一个参数开 4bit。
价值在「改动成本最低」——但**必须先靠骨架收敛到节，否则 grep 全文件会命中一堆无关行**。

### 第 4 层 · 证据分级 → 下结论

| 库 | 拿到什么 | 强度 | 在方案里的角色 |
|---|---|---|---|
| trl | 10 个开关的完整清单 | ★★★ 问题页 | **保证不漏**：先把所有杠杆列全 |
| unsloth | 三招到 500K 的具体做法 | ★★★★ 实证页 | **定上限**：知道这类模型能到什么程度 |
| swift | 一个参数切训练模式 | ★★ 参数表 | **降成本**：能改参数的别改代码 |

**这才是「三库都要看」的真正理由**：不是三家都读一遍，是**三类证据互补**——
清单保证不漏、实证给上限参照、参数表给最低成本路径。缺一类，方案就有盲区。

而这个结论**不依赖任何写死的路径**。明天官网把 `reducing_memory_usage.md` 改名，
第四层的判断照样成立，只是第 3 层要重新 `--search memory` 一次。

---

## 抗变化对照表

| 层 | 会不会烂 | 烂了怎么修 |
|---|---|---|
| 心法四层 | **不会**。依赖答案形态和设计哲学 | — |
| **目录名的功能词 → 类型** 的映射 | **基本不会**。`guide/blog/api/参数` 这些词任何文档站都要用 | 真的没了就补一个词进 `DIR_WORDS`（一个词，不是一条路径） |
| 库的切法（正交/旅程/CLI-first） | 很少。改切法等于重做文档站 | 重看 `--dirs`，重新判类型 |
| 目录名 | 偶尔（改名、合并） | `--dirs` 看新名，按功能词重判；判不出会标 `—` |
| 页面标题 | 会 | `--search <关键词>` 重找；`recipes.json` 会标 `MISS` 并 exit 1 |
| 页面路径 | 会（实测 transformers 一次迁移 25 页） | **从不记路径**，只记标题和章 |
| 参数名 | 会（版本升级） | 以本地 `docs/` 版本为准，`grep` 参数表核实 |
| **行号**（`### 量化参数 (162)` 里的 162） | **每次同步都可能变** | **只当临时辅助**，锚永远用节名/参数名，不用行号 |
| 具体数字 | 会（模型/卡/版本变了） | 数字只当参照，结论永远由本机 smoke test 给 |

**维护成本最低的是第二行**：官网改一百次目录名，只要还叫 `guide`/`blog`/`api`，
规则就不需要动。这是"给规则不给答案"带来的直接好处——
**要改的是一个词表，不是一张映射表**。

**唯一需要维护的文件是 `references/recipes.json`**，且它自带哨兵：
任何一条 `match` 找不到页面就打印 `MISS` 并 `exit 1`。
看到 MISS 不代表心法失效，只代表**缓存过期**——按心法重跑一遍、改掉 `match` 就行。

---

## 同义词表（换了词还搜不到，再怀疑文档）

`--search` 只匹配标题和路径，所以关键是**用官方的叫法**，不是你的叫法。

| 你想说的 | 文档里的词 |
|---|---|
| 显存不够 / OOM | `memory` `gradient checkpointing` `QLoRA` `4bit` `offload` `quantization` |
| 上下文拉长 | `context length` `sequence length` `packing` `rope` `long context` |
| 数据怎么摆 | `dataset` `formats` `conversational` `chat template` `template` |
| LoRA 参数 | `LoRA` `rank` `alpha` `target_modules` `hyperparameters` |
| 多卡 | `deepspeed` `zero` `fsdp` `ddp` `distributing` `multi-gpu` |
| 训练慢 | `speeding up` `packing` `throughput` `kernels` |
| 评测 | `evaluation` `eval` `benchmark` `metrics` `评测` |
| 部署 | `vllm` `inference` `deployment` `merge` `export` `推理` |
| 国产卡 | `NPU` `Ascend` `昇腾` `MUSA` `Metax` `AMD` |

命中 0 条时：换同义词 → 还不行就 `sync_docs.py --only <lib>` 重同步（可能官网新增了页）。

---

## 四条禁令

1. **不许把整篇文档读进上下文**。334 页，读完也记不住，还挤掉真正要用的。
2. **不许凭记忆写参数名**。以 `docs/` 里当前版本为准。
   实测教训（都是我犯的）：

   | 我写的 | 文档里的真实名 | 全库出现次数（实测） |
   |---|---|---|
   | `swift --train_type` | `--tuner_type` | 0 次 vs 53 次 |
   | `swift --quantization_bit` | `--quant_bits` | 0 次 vs 2 次 |

   同一件事，四个库四个叫法：trl 用 `load_in_4bit`，swift 用 `--quant_bits`，
   unsloth 用 `load_in_4bit`，bnb 配置里叫 `bnb_4bit_quant_type`。
   **凭"我觉得应该叫这个"写参数，运行时全是 `unrecognized arguments`。**
3. **不许把文档示例当本机可跑**。示例假设的卡数、版本、数据都和你不一样。
4. **不许用「文档说支持」当验证结论**。实例：`AutoModelForCausalLM` 加载多模态模型
   **不报错**，但静默丢掉视觉塔（2.21B → 1.88B）。

---

## 文档更新了怎么办

```bash
python <skill>/scripts/sync_docs.py                  # 三库增量，只下变化的页（1~3 分钟）
python <skill>/scripts/sync_docs.py --only unsloth   # 只同步一个库
python <skill>/scripts/docs_outline.py --recipes     # 跑一次，看有没有 MISS
```

索引每次按官网清单现算，所以**章节改名、页面增删都不需要改心法**。
`--recipes` 出 `MISS` 就说明缓存过期了：按上面四层重跑一遍，把新标题填回 `recipes.json`。

### 但"索引没烂"不等于"结论没烂"

文档变化分三层，**只有前两层会报错**：

| 层 | 表现 | 谁会发现 | 心法管不管用 |
|---|---|---|---|
| 索引层 | 页面增删、目录改名 | `--recipes` 出 `MISS` | 管用，重跑一次 |
| 口径层 | 参数/字段改名 | 运行时报 `unrecognized arguments` / `KeyError` | 管用，grep 概念词 |
| **结论层** | **说法变了**（"QLoRA 省 70%"在你的模型上其实是 29.5%） | **只有实测** | **心法管不了**，必须测 |

第三层最危险：**它不报错，只是悄悄地错**。你按文档的 70% 去做显存预算，实际只省 29.5%，
预留空间差了 40%——等到全量训练 OOM 时，已经过去一天了。

所以「文档变了怎么办」的完整答案是两句话：

```
索引变了   → 按心法重跑（这篇讲的全部内容）
结论变了   → 用你自己的度量做回归（10_measure.md 第三节）
```

**存一组基线数字**（s/step、峰值显存、探针分，附上当时的文档同步日期）。
库版本升级、驱动更新、重同步文档之后，跑一次 smoke test 对比：

```
同一配置，速度或显存变化 > 15%  → 有东西变了，查清再继续
同一配置，loss 曲线形状明显不同  → 训练逻辑变了，不是随机波动
```

**心法让你永远找得到那一页；度量让你知道那一页说的还算不算数。**
两者缺一个，都会在文档更新时踩坑。
