---
name: llm-sft-trainer
description: 大模型 SFT 训练的工作流导航（自包含技能包，自带 trl/unsloth/ms-swift 文档同步）。用户要训练或微调模型、问全参还是 LoRA/QLoRA、要把上下文拉长、显存不够、不知道该看哪份文档时用。流程固定：定任务 → 探环境 → 定方案 → 备数据 → 写脚本 → smoke test → 训练 → 验收；每个决策都要有实测依据，文档说的不算。触发词：训练模型、微调、SFT、LoRA、QLoRA、全参微调、长上下文训练、trl、unsloth、ms-swift、显存不够、数据集转换。
---

# llm-sft-trainer · 训练一个模型之前，先按这套流程走

## 0. 从这里开始（30 秒，能省掉十几次无用工具调用）

### 根目录怎么确定：看 SKILL.md 自己在哪，**不是看 cwd**

**本技能包的根目录 = 这个 SKILL.md 文件所在的目录。**
它和你的工作目录**经常不是一个地方**（比如你在 `D:/Code/my_task/` 干活，技能包在 `D:/Code/skills/llm-sft-trainer/`）。

所以**不要用相对路径去试**。用你已经拿到的那条绝对路径反推：

```
你读到：D:/Code/skills/llm-sft-trainer/SKILL.md
→ 根目录：D:/Code/skills/llm-sft-trainer
→ 其余都是它的子路径：
     D:/Code/skills/llm-sft-trainer/references/10_measure.md
     D:/Code/skills/llm-sft-trainer/scripts/probe_env.py
```

**一旦确定了根目录，全程用同一种写法**，不要再换（Windows 上 `D:/x/y` 最稳）。
换写法就是换坐标原点，后面每个路径都得重算。

### 六条实测踩过的坑（Qwen3.8-27B 当实习生跑出来的，见 `09_real_cases.md` 案例 15~18）

1. **不要反复 `ls` 确认结构**，直接看第 6 节的分层表。
   实测：它 `ls` 6 次、`glob` 3 次、`dir` 2 次，才确认目录里有 `references/`。
2. **路径写法只准一种**。实测它在同一次任务里试了 `D:/Code`、`/workspace/D:/Code`、
   `/Code/skills/...` 三种，每次失败都要重来一轮——**这是最贵的浪费**。
   拿不准就跑一次 `pwd`（WSL）或 `cd`（Windows），不要靠猜。
3. **读中文 md 失败时，换一种读法，不要跳过。**

   ```bash
   python -c "import io;print(io.open(r'<绝对路径>',encoding='utf-8').read())"
   # 或 cat <路径> / Get-Content <路径> -Raw
   ```

   实测：`Read` 工具对无 BOM 的 UTF-8 中文文件连失败三次，换成 python 才读到。
   **读不到就跳过，会让后面的结论建立在猜测上——这比慢更贵。**
4. **在 Windows 上调 WSL 跑训练命令时，多行脚本先写文件再执行。**
   `wsl -e bash -lc "..."` 的引号会被吃两层，heredoc 尤其必炸。
   详见 `07_platform.md` 第 8 节。

   实测：模型用 heredoc 传 12 行 python，连试三次全失败，**报告没写出来**。
   同一条命令试到第 3 次，就改成「Write 脚本到工作目录 → `wsl -e bash -lc "python 脚本.py"`」。
5. **不要用 `Read` 打开 `.png` / `.jpg` / `.gif`。**
   图是给人看的。读图片会让工具链把 base64 塞进对话上下文，
   **实测直接把整个会话打崩**（`ACP error -32603`，案例 17），后面所有步骤全废。

   需要看图的样式怎么办？**跑脚本自己生成一张**，不要读现成的：

   ```bash
   python scripts/plot_plan.py --plan plan.json --out my.png
   ```

   `assets/img/` 下那两张示例图是给人看的，跳过。
6. **bash 的输出读不到（空白 / 半截）时，把结果写进文件再用 `Read` 读，不要反复试引号。**

   ```bash
   python analyze.py > out.txt 2>&1     # 结果落盘
   ```
   然后用 `Read` 工具打开 `out.txt`——**不要用 `cat`，`cat` 也会被吞**。

   实测（案例 18）：为了打印一个 84 KB 的 json，试了十几次单引号/双引号/heredoc，
   **527 秒用完，报告一个字没写**。它每次都在"换个引号再试一次"，而不是换通道。

   **判断标准**：同一条命令换到第 3 种写法还读不到，就停止调整命令，改成落盘 + `Read`。

### 按你要做的事选读（不要全量加载）

| 你要做的 | 读这几篇 | **不用读** |
|---|---|---|
| 要一份执行计划 | 本文件 + `00_workflow` + `11_scope` | 03/04/05 的实现细节 |
| 要动手训练 | 00 → 01 → 02 → 03 → 04 | 05 的排错树 |
| 训练坏了 / 效果不对 | `05_verify` + `09_real_cases` | 01/02/03 |
| 要选方案（三档精度、哪个库） | `02_decide` + `08_cross_lib` + `12_tradeoff` | 04 的参数细节 |
| 要定"多少算够" | `11_scope` | 其他全部 |
| 要量速度/精度 | `10_measure` | 其他全部 |

**实测教训**：被要求"给一份完整计划"时，它把 9 篇 references 全读了（389 秒）。
答案是对的，但**给了计划不需要读实现细节**——按上表能省一半。

## 1. 这是什么（目的先行）

**不是训练脚本模板库，是一套固定的科研工作流。**

拿到一个模型，先探清自己的处境（几张卡、多少显存、模型多大），再**定向**去查文档，
然后定方案、备数据、写脚本、smoke test、训练、验收。

它解决的问题：不固定流程时，每次都在"先看数据？先写脚本？先查显存？"之间打转，
文档 300 多页从头翻，看着很忙，其实没进展。

三条不可动摇的原则：

- **数字必须测出来**。文档写"支持 128k"不算，你的卡能跑多长要实测。
- **一次只改一个变量**。同时改 lr 和 seq_len，效果变好你也不知道是谁的功劳。
- **三库一起看，再综合**。同一件事 trl / unsloth / swift 各有答案，覆盖范围不同
  （评测只有 swift 有专章、500k 只有 unsloth 有实测）。只看一个库，会漏掉更简单的路或更高的上限。
  看完必须给出"最简 / 最强 / 怎么组合"，见 `references/08_cross_lib.md`。

## 2. 什么时候用 / 不用

**用**（满足任一）：

- 用户要 SFT 一个模型（给了模型名/路径，或说"帮我微调"）；
- 问"全参还是 LoRA/QLoRA"、"我的显存够不够"、"上下文能拉多长"；
- 不知道 trl / unsloth / ms-swift 该看哪份文档、该用哪个库。

**不用**：

- 强化学习（DPO / GRPO / PPO）—— 本版明确不做，只查概念不实现；
- 只问某个 API 怎么调、不涉及训练决策（直接查文档即可）；
- 训练已经跑完，只问怎么部署（直接看 `references/05_verify.md` 的部署清单）。

## 3. 七步流程

| # | 步骤 | 做什么 | 产出物 | 详细文档 |
|---|---|---|---|---|
| 1 | 定任务 | 目标、数据来源、成功标准（**精度线 + 速度线 + 成本上限**） | `00_task_profile.md` | `references/00_workflow.md` |
| 2 | 探环境 | 平台（Win/WSL/Linux）、卡/显存/库/模型，全部变数字 | `01_env_report.md` | `references/01_probe.md` |
| 3 | 定方案 | 精度档位 + 上下文目标 + **三库分工与组合** + 候选对比图 | `02_training_plan.md` + `plan.png` | `references/02_decide.md` |
| 4 | 备数据 | 已有→转换；没有→生成 | `03_dataset_report.md` + 数据 | `references/03_data.md` |
| 5 | 写脚本 | 生成脚本，先 smoke test | `train.sh` / `config.yaml` | `references/04_train.md` |
| 6 | 跑训练 | 全量训练 + 记录 | `outputs/` + 日志 | — |
| 7 | 验收 | 评测、诊断、部署验证、**两维核算**；不达标按回退表往回走 | `05_eval_report.md` | `references/05_verify.md` + `12_tradeoff.md` |

**不许跳步。** 第 2 步没数字，第 3 步就是猜；第 5 步没 smoke test，全量训练就是赌博。

**但第 7 步不是终点。** 验收不达标时，要能倒推回前面某一步：

| 诊断结论 | 回到 |
|---|---|
| 数据格式错 / 样本太少 / 分布不对 | **第 4 步** |
| chat template 不一致 / lr、epoch 不合适 | **第 5 步** |
| 模型太小，能力上限不够 | **第 3 步** |
| 目标定得不可能达到 | **第 1 步** |

**每次只回一步、只改一个变量；同一问题最多 3 轮，3 轮不达标停下来问人。**
完整映射表 + 迭代预算见 `references/05_verify.md` 第六节。

### 3.1 带一个实习生：第一周只做三件事

这套流程对新人最大的风险不是学不会，是**一上来读文档、调参数、追指标**，
三天后能背出 LoRA 的 rank 取值范围，但说不出自己机器上一步要几秒。

所以第一周**不许碰效果**，只做三件能立刻验证的事：

| # | 做什么 | 验收标准 | 对应文档 |
|---|---|---|---|
| 1 | 80 条数据、跑 **20 步**、记录耗时和显存 | 能说出 s/step，且知道为什么不能用 1 步测 | `13_practice.md` |
| 2 | 同一份数据，分别跑 bf16 LoRA 和 4bit QLoRA | 能说出**自己测的**显存差和速度差，而不是文档里的 70% | `10_measure.md` 1.5 |
| 3 | 按 20 步的数字，算出 1 万条 × 3 epoch 要多久、多少 GPU-hours | 量纲对得上（步/s × 秒 = 步） | `10_measure.md` 1.6 |

**三件事都不产生"好模型"**，但做完之后，他后面每一个决策都有数字支撑。
反过来，先追指标再补度量，会养成"看着 loss 猜"的习惯，很难改。

### 3.2 怎么判断他真懂了（不问知识点，问判断）

不要问"LoRA 的 rank 是什么"——那是背诵。问这四个，答错就是没懂：

| 问 | 真懂的答 | 没懂的答（在背书） |
|---|---|---|
| "为什么 1 步测出来的速度不能用？" | 一次性开销（编译/预热）摊不开，会高估 2~4 倍 | "因为不准" / 说不出原因 |
| "QLoRA 省多少显存？" | "看模型多大，我测是 29.5%；文档的 70% 是 7B 以上的经验值" | "省 70%" |
| "显存装不下，你改哪个？ **再问：太慢，你改哪个？**" | **两个答案不一样**，且第二题是在保留 QLoRA 的前提下开 packing | 两题答同一个 / 第二题答"关掉 QLoRA" |
| "5 天优化到 4.5 天，值得花一天去做吗？" | "看机器闲不闲，也看这半天值不值" | "当然优化，越快越好" |

**第 3 题是分水岭**：显存和时间是**两个正交问题，两套手段**。
答"关掉 QLoRA 来解决慢"的，说明他把"买显存的价钱"当成了"手段的缺点"——
这种人会在 OOM 和降档之间反复横跳。

**第 4 题是第二个分水岭**：答"越快越好"的，还没建立**区间**概念，
他会在别人感知不到的差异上无限投入，看起来很勤奋，其实一直在原地。

（对应的方法：区间在 `11_scope.md`，两个正交问题在 `12_tradeoff.md` 第三节。）

### 3.3 带人的原则：给问题，不给答案

- 他问"该用哪个库" → 反问"你的显存多少、序列多长、要多快"，让他自己按 `08_cross_lib.md` 选
- 他问"文档说能跑 128k，我能吗" → 让他跑 smoke test，用结果回答
- 他说"loss 降到 0.05 了" → 问"探针集多少分"

**这套流程本身就是教具**：每一步都有产出物和闸门，他卡在哪一步，就是哪一块没懂，不用猜。

## 4. 第一次用：先同步文档

技能包**不带文档**，只带脚本。第一次用或文档过期时：

```bash
python scripts/sync_docs.py
```

三个库（trl / unsloth / ms-swift）约 1~3 分钟。文档落在 `docs/`，已被 `.gitignore` 排除。
之后用 `--only` 增量更新，或 `--page <URL>` 抓单页补漏。

技能自身只依赖 `requests`；torch 和训练库装在**每个训练工作目录**下，不混在一起
（原因见 `references/01_probe.md`）。

## 5. 五条硬约束（不许例外）

1. **精度档位**：全参跑不动就 LoRA，LoRA 跑不动就 QLoRA。不许不看数字硬试全参。
2. **必须能部署**：最终要能上 vLLM；部署方式和四项实测见 `references/05_verify.md`。
   注意 QLoRA 的 4bit 只在训练阶段，部署加载的是 bf16 基座 + adapter。
3. **不许凭记忆**：参数名、库 API、默认值，一律以 `docs/` 里当前版本为准。
4. **不许只看一个库**：每个决策点都要摆出三库各自的对应页，再给综合结论。
   方案里必须写"为什么不用另外两条"（是看了不选，不是没看）。
   某库确实没有对应页是正常的，标注原因即可，不要硬凑一篇、也不要把另外两个当"备选"。
5. **探索有预算：一个事实最多验 3 次，超了就必须往下走。**

   ```
   第 1 次没确认  → 换一种方法（换命令 / 换文件 / 读源码）
   第 2 次没确认  → 缩小问题（只验一个字段，不验整条链路）
   第 3 次没确认  → 停。按最保守的选项决策，在产出物里写：
                    「未验证｜我假设 X，依据是文档 Y；风险：如果假设错了，会影响 Z」
                    然后继续下一步。
   ```

   **为什么这条最容易被忽略**：验证本身看起来像在工作，而且每一步都"很合理"。
   但七步流程里，**每一步都要有产出物**——产出物没有，验证再多也等于零。

   **实测代价**（`09_real_cases.md` 案例 16）：让 Qwen3.8-27B 跑完整七步，
   第 4 步为确认 `assistant_only_loss` 的行为花了 29 次调用，第 5 步接着花 20 次写了 5 个
   `*_probe.py`，**训练脚本始终没写出来**。第 6 步一看没脚本，只能停。
   三步加起来 15 分钟，产出为零——而它每一步的验证动作单独看都是对的。

   **判断标准**：如果你发现自己连续写的三个脚本都叫 `*_probe.py`，就已经在兔子洞里了。

## 6. 分层文档（按需读，不要全量加载）

| 文件 | 管什么 | 什么时候读 |
|---|---|---|
| `references/00_workflow.md` | 七步流程、每步闸门、任务模板 | 开工前 |
| `references/01_probe.md` | 两套环境、探测清单、模型下载规则 | 第 2 步 |
| `references/02_decide.md` | 三档精度、长上下文阶梯、三库分工与组合、vLLM 部署表 | 第 3 步 |
| `references/03_data.md` | 数据体检、格式映射、生成规格、label mask | 第 4 步 |
| `references/04_train.md` | 三库骨架、参数判据、显存开关顺序、smoke test | 第 5 步 |
| `references/05_verify.md` | 指标、诊断树、部署验证清单 | 第 7 步 |
| `references/06_docs_nav.md` | **心法四层**（定性 / 认骨架 / 进页 / 证据分级）+ 完整演绎 + 抗变化对照 | 每次要查文档时，先读这篇 |
| `references/07_platform.md` | Windows / WSL2 / Linux 怎么选、WSL 兼容坑 | 第 2 步，或环境报错时 |
| `references/08_cross_lib.md` | 三库分工矩阵、三条组合配方、组合禁忌 | 第 3 步定方案时必读 |
| `references/09_real_cases.md` | 18 条真实正反例（环境耦合、白复制模型、多模态 config、target_modules 只覆盖 4.5%、拼接日志、测速窗口、混用口径、**把两个正交维度折成一个成本**、**27B 模型当实习生的实测**） | 每步自查，尤其第 3 / 5 步 |
| `references/10_measure.md` | **怎么量**：速度三层（含测速窗口、口径统一）、精度四层证据、交叉判读、度量做回归 | 第 5 步测速、第 7 步验收 |
| `references/11_scope.md` | **多少算够**：可接受是区间不是线、现实分 A/B/C、三个维度怎么量、跨档才优化 | 第 1 步定区间 |
| `references/12_tradeoff.md` | **该省哪个**：三本账、省显存/省时间两张手段表、反例、档内不排序、**选型图怎么画/怎么读** | 显存不够 / 太慢 / 要选方案时 |
| `references/13_practice.md` | **练习**：30 分钟把 10-12 串一遍，五问 + 判分 + 现实题 | 第一次带人，或自己上手前 |
| `references/14_selftest.md` | **自测这套文档**：四个任务（link/plan/trap/exec）、三个入口指标、回归基线 | 改完 skill 之后，或换模型验证 |

**10-13 是同一件事拆成的四篇**，顺序就是读的顺序：先会量（10）→ 再定够不够（11）→ 再选手段（12）→ 最后练（13）。
**不要跳着读 12**——不先知道"卡在哪个维度"，选手段就是瞎选。

**查文档的顺序是「先方法、后缓存」**：

1. **先读 `06_docs_nav.md` 的心法四层** —— 定性（A有没有/B怎么配/C值多少/D怎么搭）→ 认骨架（这个库按什么维度分目录）
   → **渐进读**（文件夹约 720 tok 一次给全 → 页 → 节 → 正文按 10k 分块）→ 证据分级
2. **`--recipes` / `--matrix` 只是缓存** —— 它是某次按心法跑出来的结果，省你重跑。
   出 `MISS` 就是缓存过期，按心法重跑一遍即可，**不要把它当真理**
3. **绝不要背"某问题→某文件"的表** —— 官网改组过一次 25 页，表全废，心法不变

## 7. 脚本速查

```bash
# 文档同步（技能包自带，增量）
python scripts/sync_docs.py                          # 三库全同步
python scripts/sync_docs.py --only unsloth           # 只同步一个
python scripts/sync_docs.py --list                   # 只列清单不下载
python scripts/sync_docs.py --page <官方URL>          # 只抓一页
python scripts/sync_docs.py --force                  # 强制重写

# 查文档 —— 按「文件夹 → 页 → 节 → 正文块」逐层收窄，每层先看代价
python scripts/docs_outline.py --dirs                # L0 文件夹结构 + 类型标注，全表约 700 tok
python scripts/docs_outline.py --dirs --type 配方     # 只留「配方」类目录（219 tok）
python scripts/docs_outline.py --dirs --type 实证     # 只留「实证」类目录（44 tok）
python scripts/docs_outline.py --toc --in how_to_guides   # L1 该目录下有哪些页
python scripts/docs_outline.py --headings <md路径>    # L2 这页分几节（到 ####）
python scripts/docs_outline.py --read <md路径>        # L3 读正文：≤10k 整篇，>10k 给第 1 块
python scripts/docs_outline.py --read <md路径> --chunk 2   # 下一块

# 缓存（省你重跑，过期按心法重算）
python scripts/docs_outline.py --matrix              # 19 场景 × 三库覆盖 × 最简/最强
python scripts/docs_outline.py --recipes             # 详细版：三库并列 + 组合 + 禁忌
python scripts/docs_outline.py --recipes --step 3    # 只看某一步
python scripts/docs_outline.py --search lora         # 按关键词找页（配 --in 锁目录）
python scripts/docs_outline.py                       # 三库一级目录 + 页数

# 环境与模型（只报事实）
python scripts/probe_env.py                          # 平台/GPU/显存/库/磁盘
python scripts/probe_model.py <path|HF id> --seq-len 4096
python scripts/check_dataset.py data/train.jsonl --tokenizer <id> --seq-len 4096

# 第 5 步训练脚本：从实测跑通的模板起步，按你的场景改（见 04_train.md §7）
cp templates/train.py templates/train.sh templates/config.example.yaml <你的训练目录>/
#   train.py    训练主体：smoke/full 共用，已内置 s/step 双口径、eval() 生成、think 开关
#   train.sh    smoke / full 两模式启动命令
#   config.example.yaml → 复制成 config.yaml，改模型/数据路径/显存上限
#   实战结束必须把这三份全文交付给用户，写在磁盘上不算交付

# 选型图（第 3 步产出物配图，需要 matplotlib）
python scripts/plot_plan.py --plan plan.json --out plan.png --objective vram
python scripts/plot_plan.py --plan plan.json --objective time    # 目标换时间，结论可能反过来
#   四格：显存 / 时间 / 精度 各一格条形 + 一格两维散点（不折成一个综合分）
#   红条=选定，绿条=可行，灰斜纹=违反硬约束，* 后缀=估算值
#   精度最高的方案可能是灰的——它超了别的维度，再准也进不了候选
#   自动判断目标维度是否真的紧（占上限 <70% 判为不紧，改提示省另一维）

# 自测这套文档（可选，不参与训练流程）
python scripts/selftest.py --list                    # 看四个探针任务
python scripts/selftest.py --endpoint <vLLM地址> --task link     # 最快，约 1 分钟
python scripts/selftest.py --endpoint <vLLM地址> --task all      # 全跑，约 20 分钟
python scripts/selftest_e2e.py --endpoint <vLLM地址> --workdir <已备好的目录> \
    --train-python <装了torch的python> --model-path <模型路径>     # 完整七步，约 90 分钟
```

**两个自测脚本分工不同**：
- `selftest.py`：四个探针（link/plan/trap/exec），分钟级，**改完文档快速回归**
- `selftest_e2e.py`：完整七步，小时级，**大改后验全流程**（会真训练一次）

判据见 `references/14_selftest.md` 第三节（重复率 > 30% 就是入口不清楚）。

## 8. 目录结构

```
llm-sft-trainer/
├── SKILL.md                 入口（本文件）
├── README.md                给人看的安装与使用说明
├── scripts/
│   ├── sync_docs.py         三库文档同步（trl / unsloth / ms-swift）
│   ├── docs_outline.py      目录结构导出（离线读 manifest）
│   ├── probe_env.py         环境探测（平台/GPU/库，只用标准库）
│   ├── probe_model.py       模型结构 + 三档显存粗估
│   ├── check_dataset.py     数据集体检（token 分布）
│   ├── selftest.py          自测：四个探针（分钟级，可选）
│   └── selftest_e2e.py      自测：完整七步，会真训练（小时级，可选）
├── templates/               ★ 训练脚本模板（第 5 步复制起步，实测跑通泛化）
│   ├── train.py             训练主体：smoke/full 共用，s/step 双口径+eval 生成+think 开关
│   ├── train.sh             启动命令：smoke / full 两模式
│   └── config.example.yaml  超参样例 → 复制成 config.yaml 按场景改
├── references/              15 篇流程文档 + recipes.json（缓存）
├── assets/img/              选型图示例（**给人看的，不要 Read**）
├── docs/                    运行时下载的官方文档（不入库）
└── models/                  可选：下载的模型权重（不入库）
```

训练产物**不放在技能包里**，放在每次训练自己的工作目录（结构见 `00_workflow.md`）。

## 9. 已知待建

- **RL 层**（DPO / GRPO / PPO）：本版明确不做，等 SFT 流程跑顺再说。
- **多模态训练**：只在关键词表里留了入口，没有独立流程。
- **自动选基座**：现在依赖用户指定，或按任务人工选。
- **训练日志解析**：loss 曲线、OOM 定位目前靠人看日志，没做自动诊断脚本。

这几项都是"见过真实需求再建"，不预先编规则。
