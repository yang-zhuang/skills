# 03 · 备数据：先体检，再转换，最后才谈生成

## 两条路，先分清

```
用户给了数据？
├── 有 → 体检（格式 + token 分布）→ 映射成目标库要的 schema → 再体检
└── 没有 → 定义生成规格 → 生成 → 人工抽检 → 体检 → 映射
```

**不要把"生成数据"当默认动作**。生成的数据没有质量兜底，能用人给的就用人给的。

## 一、体检：先知道手里是什么

```bash
python <skill>/scripts/check_dataset.py data/train.jsonl \
    --tokenizer Qwen/Qwen2.5-7B-Instruct --seq-len 4096
```

输出会告诉你三件事：

1. **格式**：alpaca / sharegpt / openai-messages / prompt-completion / text
2. **token 分布**：min / p50 / p90 / p99 / max
3. **超长比例**：有多少条超过你计划的 seq_len

### 怎么用这三个数

| 超长比例 | 结论 |
|---|---|
| 0% | 不用截断；可以考虑 packing 提吞吐 |
| ≤ 1% | 直接截断，损失可忽略 |
| 1%~10% | 先查这些超长样本是不是脏数据；不是就提 seq_len 或分段 |
| > 10% | 截断会丢大量内容，必须重新切分或提长度 |

另外两个信号：

- `max > p50 × 10`：长度极不均，短样本 padding 会白烧显存 → 按长度分桶或开 packing
- 样本数 < 500：SFT 极易过拟合，先确认这不是玩具集

## 二、映射：转成目标库要的 schema

三个库的入口格式不一样，但都能吃"对话"或"纯文本"。**以各库当前文档为准**。

### 依据怎么找

「数据该长什么样」是 **A 类（有没有这个格式）**。按 `06_docs_nav.md` 第二层的规则，
A 类先找**概念 / 手册 / 定制**三类目录：

```bash
python scripts/docs_outline.py --dirs --type 概念     # 24 tok
python scripts/docs_outline.py --dirs --type 手册     # 54 tok
python scripts/docs_outline.py --dirs --type 定制     # 23 tok
```

真实输出：

```
=== trl（57 页 / 10 个目录，只看「概念」）===
    01_conceptual_guides/     3  概念

=== trl（...只看「手册」）===
    06_api/          5  手册
=== swift（...只看「手册」）===
    Instruction/    39  手册

=== swift（...只看「定制」）===
    Customization/     3  定制
```

**读出来的事实**：unsloth **三类都没有**——它的数据说明混在配方目录里
（`get-started/fine-tuning-llms-guide`）。不是漏，是它的组织方式不同。

于是走 06 的兜底：**类型找不到 → 按标题搜**。

```bash
python scripts/docs_outline.py --search dataset --lib unsloth
#  →  get-started · Datasets Guide
```

**实查结果（2026-11-16，按上面路径查到的）**

| 库 | 从哪个类型目录找到 | 页面 | 常见格式 |
|---|---|---|---|
| trl | 概念 | `Dataset Formats`、`Chat Templates` | conversational / standard / prompt-completion |
| unsloth | **兜底：标题搜**（三类目录都没有） | `Datasets Guide` | Alpaca / ShareGPT / ChatML |
| ms-swift | 定制 + 手册 | `自定义数据集`、`命令行参数` 的 `### 数据参数` | jsonl，`messages` 或 query/response |

比原来的表多一列「从哪个类型目录找到」——**那一列是推导过程**。
只有页面名的表，抄完下一版官网改名，就不知道从哪重找了。

转换时有三件事必须一起做，不能只改字段名：

### 1. 套 chat template，别自己拼字符串

```python
text = tokenizer.apply_chat_template(messages, tokenize=False,
                                     add_generation_prompt=False)
```

手拼 `"<|im_start|>user\n..."` 在换模型时必错，而且容易漏掉 BOS/EOS。

### 2. 决定 label mask：只学回答，还是连问题一起学

| 策略 | 效果 | 什么时候用 |
|---|---|---|
| 只对 assistant 回复算 loss | 模型专注学"怎么答" | **默认推荐** |
| 全序列算 loss | 连提问方式一起学 | 数据量很小、想让模型模仿整体风格 |

三库的开关名不一样，别混：

| 库 | 怎么控制 | 默认行为 |
|---|---|---|
| trl | `assistant_only_loss` / `train_on_responses_only` | 全序列算 loss，要显式打开 |
| unsloth | `train_on_responses_only` | 全序列算 loss，要显式打开 |
| swift | `--loss_scale`：`default` / `last_round` / `all` | SFT 默认 `default`＝system/user/多模态 token 不算 loss |

**这是最容易漏的一步**——漏了不会报错，但模型会把大量容量花在背问题上。
反过来，swift 的默认值已经很合理，别照搬 trl 的写法又叠一层。

#### 这一步只做「开/不开」的决策，**不要在这里验证库的内部实现**

| 该在第 4 步做的 | 该留到第 5 步做的 |
|---|---|
| 决定 `assistant_only_loss=True`（默认推荐） | 确认这个版本的参数名拼写（`--help` / 查参数表） |
| 在报告里写清"为什么这么选" | 确认 trl 会不会自动 patch chat template |
| 记下"未验证：跑起来要看 loss 是否只算回答" | 打印一条 tokenized 样本，看 labels 里 -100 的占比 |

**为什么强调这个分工**：`assistant_only_loss` 背后牵着 chat template 的
`{% generation %}` 标记、trl 的自动 patch 逻辑、不同模型族的差异——**这是个无底洞**。

实测代价（`09_real_cases.md` 案例 16）：让 Qwen3.8-27B 跑完整流程，
它在第 4 步为确认这个参数花了 29 次调用，第 5 步又花 20 次写了 5 个 `*_probe.py`，
**最后训练脚本没写出来**，第 6 步只能停。每一步单独看都很"严谨"，合起来是零产出。

**判据**：跑通一次比读十遍源码有用。
20 步 smoke test 里打印一条样本的 `labels`，看 assistant 段是不是非 `-100`——
这是 5 行代码的事，不是读源码的事。

### 3. 特殊 token 对齐

- 确认 `pad_token` 存在（很多模型没有，要设成 `eos_token`）
- 确认 chat template 里的特殊 token 在词表里
- 确认 `eos_token` 加在了每条样本末尾

## 三、没有数据：先写生成规格，再生成

生成前必须定死这五项，写进 `03_dataset_report.md`：

| 项 | 例子 | 为什么必须定 |
|---|---|---|
| 数量 | 2000~5000 条 | 太少学不会，太多生成成本高 |
| 任务类型分布 | 问答 60% / 改写 20% / 拒答 20% | 防止某一类淹没其它类 |
| 难度分布 | 简单 30% / 中等 50% / 困难 20% | 全简单会欠拟合难例 |
| 输入长度 | 50~800 token | 决定 seq_len 和 packing |
| 输出风格 | 中文、分点、不超过 300 字 | 不定就会风格漂移 |

生成后**必须人工抽检至少 20 条**，看四件事：

- 事实对不对（有没有编造）
- 格式是否统一
- 有没有把评测集内容泄漏进来
- 有没有重复样本

抽检不过就重生成，别硬着头皮训。

## 四、数据质量清单（转换完再跑一遍）

- [ ] 空样本数为 0
- [ ] 完全重复的样本已去重
- [ ] `input` 里没有包含 `output`（标签泄漏）
- [ ] 每条样本末尾有 EOS
- [ ] 特殊 token 不与正文冲突
- [ ] 训练集和评测集**没有重叠**（用 hash 或近似去重查）
- [ ] 语言/风格与目标一致
- [ ] token 分布重新统计过（转换会改变长度）

## 五、产出物

`03_dataset_report.md`：

```markdown
# 数据报告

## 来源
- 用户提供：data/raw.jsonl（3200 条）
- 无生成数据

## 体检（转换前）
- 格式：sharegpt
- p50 / p90 / p99 / max：420 / 980 / 2100 / 5400
- 超过 seq_len=4096：12 条（0.4%）→ 直接截断

## 转换
- 目标 schema：trl conversational（messages）
- chat template：Qwen2.5 自带
- label mask：只对 assistant 算 loss
- 输出：data/train.jsonl（3188 条，12 条截断）

## 体检（转换后）
- p50 / p90 / p99 / max：450 / 1010 / 2080 / 4096
- 空样本 0，重复 0，EOS 缺失 0

## 待确认
- [ ] 评测集与训练集无重叠
```
