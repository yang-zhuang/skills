# 内容类型覆盖测试（2026-10-01，batch_021~024，共 28 篇）

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

## 三、四批测试

| 批次 | seed | 抽样方式 | 篇数 | 目的 |
|---|---|---|---|---|
| batch_021 | 1014 | random 6 | 6 | 收口后首个随机批（对照） |
| batch_022 | 1015 | strata 2 × (survey, theory, nonstandard) | 6 | 补低频三型空白 |
| batch_023 | 1016 | strata 1（8 型各 1） | 8 | 广度轮 1 |
| batch_024 | 1017 | strata 1（8 型各 1，换样本） | 8 | 广度轮 2：换样本验稳定性 |

全部锁 `prompt_sha1=e1c4a9980fa4` / `rubric_sha1=9ce742e90e99`（三条裁决后的现行标准）。
四批均：解析失败 0、缺键 0、**引用核验 28/28**。

## 四、结果：按类型看

| 类型 | 篇数 | violation | rubric_issue | 判定 |
|---|---|---|---|---|
| nonstandard 非标准 | 5 | **1** | 2 | ⚠️ 唯一出 violation 的类型 |
| survey 综述 | 4 | 0 | 0 | ✅ 首次实测，4/4 干净 |
| resource 资源型 | 2 | 0 | 0 | ✅ |
| system 应用型 | 4 | 0 | 2 | ⚠️ 条款歧义，非违规 |
| theory 理论 | 4 | 0 | 1 | ⚠️ 条款歧义，非违规 |
| analysis 机制分析 | 4 | 0 | 0 | ✅ |
| method 方法模型 | 3 | 0 | 1 | ⚠️ 条款歧义，非违规 |
| empirical 实验 | 2 | 0 | 0 | ✅ |
| **合计** | **28** | **1** | **6** | violation 率 3.6% |

逐批：batch_021 violation 0/6（ri 2）、batch_022 violation **1/6**（ri 1）、
batch_023 violation 0/8（ri 3）、batch_024 violation **0/8（ri 0，全清）**。

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

1. **综述类的行为是干净的**：4 篇综述全部落 `不训练` + `base_model`/`data_scale` 留空，
   执行端与评审端一致，条款 4 的「综述 → `不训练`」边界首次拿到实证。
2. **薄弱点是 nonstandard，不是 survey**。thesis proposal（acl-srw）里方法常被写成
   「将要做什么」，模型名常以泛称或被引工作出现，执行端容易把「相关工作的模型」误当宿主。
   5 篇里 1 篇违规、2 篇报歧义 —— 这是当前 rubric 覆盖率最低的地方。
3. **广度轮 2（batch_024）全清**，说明轮 1 的 3 条 rubric_issue 是样本触发的条款缝隙，
   不是系统性回退。

## 六、落档与去向

- 本报告：`runs/coverage_20261001_types.md`，包内副本 `references/` 同名文件。
- 新增脚本 `classify_papers.py` 与 `prepare_batch.py` 的 `--strata` / `--types` 参数，
  待回灌技能包并更新 `scripts/BASE.txt` 指纹。
- 未改任何 rubric 或 EXTRACT —— §3 第三槽那条已跨批 2 次，与 §5 / §4 的条款缝一起
  按纪律走人裁。
