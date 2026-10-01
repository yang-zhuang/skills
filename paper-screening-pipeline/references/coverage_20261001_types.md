# 内容类型覆盖测试（2026-10-01，batch_021~029，共 52 篇）

## 一、起因：随机抽样会饿死低频类型

此前 21 个批次全部是 `--seed N --n 6` 纯随机抽样。用户问「是否测过综述、纯理论、机制、
实验、应用型、非标准论文」，把全池按内容类型分桶后核历史覆盖，结果是：

| 类型 | 池内 | 历史已测 | 覆盖率 |
|---|---|---|---|
| nonstandard 非标准 | 93 | 4 | 4.3% |
| **survey 综述** | **21** | **0** | **0.0%** ← 从未测过 |
| resource 资源型 | 187 | 9 | 4.8% |
| system 应用型 | 207 | 7 | 3.4% |
| theory 理论 | 41 | 2 | 4.9% |
| analysis 机制分析 | 159 | 8 | 5.0% |
| method 方法模型 | 598 | 27 | 4.5% |
| empirical 实验 | 265 | 13 | 4.9% |

两个结论：

1. **不是「类型偏斜」，是「低频型被饿死」**。各类型覆盖率其实都在 3.4%~5.0% 之间，
   与池内占比无关 —— 随机抽样是公平的。问题在于公平地摊薄：70 篇分给 8 型，
   survey 只有 21 篇池，按 4.5% 覆盖算期望不到 1 篇，实测就是 0 篇。
2. **survey 类从未被测过**。而「综述该填 `不训练` 还是 `[]`」恰恰是条款 4 的核心边界
   之一（条款里白纸黑字写着「只做评测/分析/综述 → `不训练`」），却从没有一篇综述实证过它。

## 二、做法：加一个确定性分类器 + 分层抽样

新增 `classify_papers.py`（纯规则、不调用 LLM）：只用 venue、标题、摘要、全文章节标题
三类本地信号，按**优先级链**判一个类型（命中即停）：

```
nonstandard > survey > resource > system > theory > analysis > method > empirical
```

- `nonstandard` 走结构性判定优先（venue = acl-srw/acl-tutorials、摘要过短），
  因为这类文本上任何内容信号都不可靠；
- `empirical` 是兜底类，不参与前面的判定。

输出 `runs/paper_types.jsonl`（paper_id + venue + type + 命中信号），可单篇诊断
（`--pid`）也可按型列举（`--types`）。

`prepare_batch.py` 加 `--strata N`（每型抽 N 篇）与 `--types`（限定参与分层的类型），
manifest 新增 `selector`、`type_quota`、`paper_types` 三个字段 —— 跨批对账时可按类型
看覆盖，而不是只看篇数。

全池 1571 篇的分布：

```
method 38.1% | empirical 16.9% | system 13.2% | resource 11.9% | analysis 10.1%
nonstandard 5.9% | theory 2.6% | survey 1.3%
```

survey 只占 1.3%，纯随机抽样连跑 20 批的期望命中不到 3 篇 —— 这就是为什么必须分层。

## 三、九批测试

| 批次 | seed | 抽样方式 | 篇数 | 目的 |
|---|---|---|---|---|
| batch_021 | 1014 | random 6 | 6 | 收口后首个随机批（对照） |
| batch_022 | 1015 | strata 2 × (survey, theory, nonstandard) | 6 | 补低频三型空白 |
| batch_023 | 1016 | strata 1（8 型各 1） | 8 | 广度轮 1 |
| batch_024 | 1017 | strata 1（8 型各 1，换样本） | 8 | 广度轮 2：换样本验稳定性 |
| batch_025 | 1018 | strata 3 × nonstandard | 3 | 攻唯一出违规的类型（见第六节） |
| batch_026 | 1019 | strata 2 × (survey, theory, nonstandard) | 6 | 低频三型加量（见第六节） |
| batch_027 | 1020 | strata 2 × (system, analysis, resource) | 6 | §5 最常触发的三型（见第七节） |
| batch_028 | 1021 | strata 3 × nonstandard | 3 | 最弱类型继续加量（见第七节） |
| batch_029 | 1022 | strata 3 × (method, empirical) | 6 | 池内占比最大的两型（见第七节） |

全部锁 `prompt_sha1=e1c4a9980fa4` / `rubric_sha1=9ce742e90e99`（三条裁决后的现行标准）。
九批均：解析失败 0、缺键 0；**引用核验 52/52**（其中 batch_029 有 2 份首次未过、重发评审后通过，
并暴露了空值 current 无法核验的契约漏洞，已修，见第七节）。

## 四、结果：按类型看

| 类型 | 篇数 | violation | rubric_issue | 判定 |
|---|---|---|---|---|
| nonstandard 非标准 | **13** | **2** | 6 | ⚠️ 方法常被写成「将要做什么」、模型名常是被引工作 |
| survey 综述 | **8** | 0 | 0 | ✅ 8/8 干净 |
| resource 资源型 | 4 | 0 | 2 | ⚠️ §6 benchmark 的 train split 算不算训练量 |
| system 应用型 | 6 | 0 | 2 | ⚠️ 条款歧义，非违规 |
| theory 理论 | **8** | 0 | 1 | ⚠️ 条款歧义，非违规 |
| analysis 机制分析 | 6 | 0 | 2 | ⚠️ §4 判别模型 vs 探测探针 |
| method 方法模型 | **6** | **2** | 3 | ⚠️ **加量后冒头：6 篇 2 违规（33%）** |
| empirical 实验 | 5 | 0 | 0 | ✅ |
| **合计** | **52** | **4** | **16** | violation 率 7.7% |

逐批：batch_021 0/6（ri 2）、batch_022 **1/6**（ri 1）、batch_023 0/8（ri 3）、
batch_024 **0/8（ri 0，全清）**、batch_025 0/3（ri 2）、batch_026 **1/6**（ri 1）、
batch_027 0/6（ri 4）、batch_028 0/3（ri 1）、batch_029 **2/6**（ri 2）。

### 唯一那条 violation

`2026.acl-srw.120`（nonstandard / thesis proposal）：执行端填 `base_model = "HRM, TRM"`，
评审判违规 —— HRM/TRM 只以被引相关工作与 `a TRM/HRM-style model` 泛称出现，没有
「本文方法施加于该模型」的自述。

### rubric_issue 的落点（6 条）

- §5 `base_model` 4 条：模型名出现在章节标题/附录标题、plug-and-play 调用 vs 被动审计
  对象的区分、评测句里同时被微调的模型归哪侧
- §4 `training_paradigm` 1 条：`distill` 的比喻用法（`distill features from noise`）不算
  师生蒸馏信号词
- §3 `method_summary` 1 条：模板第三槽「〈关键规模/结果〉」在材料未披露时能否缺省
  （**第 2 次复现**：b016 的 `acl-srw.78` + 本轮 `findings-acl.1154`，已达动手阈值）

## 五、结论

1. **综述类的行为是干净的**：8 篇综述全部落 `不训练` + `base_model`/`data_scale` 留空，
   执行端与评审端一致，条款 4 的「综述 → `不训练`」边界已拿到 8 篇实证。
2. **nonstandard 仍是覆盖率最低的类型**：thesis proposal（acl-srw）里方法常被写成
   「将要做什么」，模型名常以泛称或被引工作出现，执行端容易把「相关工作的模型」误当宿主。
   13 篇里 2 篇违规、6 篇报歧义。
3. **广度轮 2（batch_024）全清**，说明轮 1 的 3 条 rubric_issue 是样本触发的条款缝隙，
   不是系统性回退。
4. **「测过且干净」不等于「这类没问题」**（第三轮的核心教训）：method 型前 3 篇零违规，
   加量到 6 篇立刻冒出 2 条（33%）。判定一个类型是否安全，看的不是它出过几次违规，
   而是**测过多少篇** —— 6 篇以下的类型结论都只是抽样噪声。

## 六、第二轮：nonstandard 加量与低频型复测（batch_025 / 026）

| 批次 | seed | 抽样方式 | 篇数 | 目的 |
|---|---|---|---|---|
| batch_025 | 1018 | strata 3 × nonstandard | 3 | 攻上一轮唯一出 violation 的类型 |
| batch_026 | 1019 | strata 2 × (survey, theory, nonstandard) | 6 | 低频三型加量 |

batch_025 violation 0/3（引用核验 3/3），batch_026 violation 1/6（核验 6/6）。
两批 9 篇 violation 1 → 11.1%，**仍全部落在 nonstandard**；survey 追加 2 篇后累计 6/6 干净。

### 那条 violation（`2026.acl-srw.47`，nonstandard）

- 抽取填 `training_paradigm = ["不训练"]`
- 评审判 §4 违规、建议 `[]`：原文「a more careful setup than the standard
  "train a text classifier and report AUC" recipe」说明训练动作并未取消，
  但全文只有泛化的 `train`，无 `fine-tune` / `RLHF` / `DPO` 等任一范式族信号词，
  按推定边界条应落 `[]`
- **冲突点**：§4 最近一次人裁刚把「判别模型/预测器 `train a classifier`」并入 SFT 外延，
  而推定边界条说「表内一个信号词都没有时禁止推定 → `[]`」。同一句话两条规则给出
  `SFT` 与 `[]` 两个答案 —— 这是 rubric 自相矛盾，不是执行端抽错

### 第二轮的两条 rubric_issue

- `2026.acl-srw.6` §1：上限「不超过 6 字」与正例 `多模态情绪识别`（7 字）直接冲突——
  该条修订记录里自己写着「与正例（6~7 字）自相矛盾」，却仍把上限留在 6 字，没修干净
- `2026.acl-tutorials.6` §2：教程 / 综述 / position 类原文不陈述痛点缺口时，
  research_question 没有「信息不足」出口，只能在硬编缺口句与留空之间二选一

## 七、第三轮：中高频型加量（batch_027 / 028 / 029，15 篇）

| 批次 | seed | 抽样方式 | 篇数 | 目的 |
|---|---|---|---|---|
| batch_027 | 1020 | strata 2 × (system, analysis, resource) | 6 | §5 最常触发的三型 |
| batch_028 | 1021 | strata 3 × nonstandard | 3 | 最弱类型继续加量 |
| batch_029 | 1022 | strata 3 × (method, empirical) | 6 | 池内占比最大的两型（38% / 17%）|

结果：batch_027 violation 0/6（ri 4，含**同批高频**的 §6 覆盖缺失 2 篇）、
batch_028 violation 0/3（ri 1）、batch_029 violation **2/6**（ri 2）。

**最重要的变化：method 型从「干净」变成「最脏」。** 此前 method 只测过 3 篇、零违规，
加量到 6 篇后冒出 2 条——两条都落在 §4 / §5 的老缝隙上：

1. `2026.acl-long.1578`（method）：抽取 `training_paradigm = ["SFT"]`，评审判应落 `[]`
   ——原文只有泛化动词 `train`，无任一范式族信号词。**与 b026 的 `acl-srw.47` 同向**，
   该家族已累计 5 批（b007, b011, b015, b026, b029）。
2. `2026.findings-acl.1200`（method）：抽取 `base_model = ""`，评审判应填 `GPT-4o-mini`
   ——模型名只出现在「SP@1 从 60.21% 升至 66.70%」这类**增益结果句**里。与 b004 同向、
   与 b022 反向（b022 判的是「应留空」），**§5 内部方向分裂**。

顺带修掉一个脚本侧契约漏洞：`1200` 这类「原值本就是空字符串」的 violation，
`current` 无从逐字比对，核验必然失败。改为**按字段名核验**（`current` 留空 + 必给 `field`，
脚本核对 `fields.json` 里该字段确为空）。`cluster_reviews.py` 与 `EVALUATION.md` 均已同步。

## 八、待裁候选（按聚集度排序，截至 batch_029）

| 条款 | 跨批聚集 | 具体情形 | 判据 |
|---|---|---|---|
| §4 `training_paradigm`（rubric_issue） | **14 批**（b002,003,006,007,008,011,014,015,019,021,023,027,028,029） | 判别模型/探针/生成式模型/两阶段取值/信号词表是否穷举 | 聚集最高 |
| §5 `base_model`（rubric_issue） | **12 批**（b002…b029） | 什么算可指认标识：泛称、架构名带后缀、章节标题、结果句、对抗攻击目标 | **建议方向互相矛盾**，最需要人裁 |
| §4 泛化训练动词（violation） | 7 批（b002,003,007,011,015,026,029） | `train` / `train a classifier` → `[]` 还是 `SFT` | 5 批同向（`[]`），**已达动手阈值**，但与上轮裁决冲突 |
| §5 violation | 3 批（b004,b022,b029） | 模型名只在增益结果句出现时该不该填 | 方向分裂，随 §5 一起裁 |
| §6 `data_scale` | 4 批（b002,014,017,027）+ b027 同批高频 2 篇 | benchmark 自带 train split 算不算训练数据量 | 同批跨 2 篇复发 |
| §3 模板第三槽缺省 | 2 批（b016 的 srw.78 + b022 的 findings-acl.1154） | 材料无规模/结果时第三槽能否缺省 | 已达动手阈值 |
| §2 教程/综述的「信息不足」出口 | 2 批（b002,b025） | 无缺口陈述时 research_question 怎么填 | 观察中 |
| §1 字数上限 6 字 vs 正例 7 字 | 1 批（b025） | 修订记录自认矛盾却未改 | 事实矛盾，可顺手修 |

## 九、落档与去向

- 本报告：`runs/coverage_20261001_types.md`，包内副本 `references/` 同名文件。
- 新增脚本 `classify_papers.py` 与 `prepare_batch.py` 的 `--strata` / `--types` 参数，
  待回灌技能包并更新 `scripts/BASE.txt` 指纹。
- 未改任何 rubric 或 EXTRACT —— §3 第三槽那条已跨批 2 次，与 §5 / §4 的条款缝一起
  按纪律走人裁。
