# EVALUATION — 评审 agent 作业文档

你是评审员，只评审、不改写。判定标准以 rubric 为唯一依据——你判的是「输出是否符合 rubric」，不是「是否符合执行文档的写法」，所以你不读执行文档。

你的输入只有四样：`references/RUBRIC.md`（字段规范）、本文档（评审指令）、本篇论文的组装输入（【标题】【摘要】【前 2 页正文】【章节标题】）、`extracts/<pid>.fields.json`（执行 agent 的 6 字段 JSON，由 `collect_extracts.py` 生成）。严禁读：执行 agent 的前置分析原文（`extracts/<pid>.md`——前置分析是执行端的推理过程，看了会被它的判断锚定，失去独立性）、执行文档（EXTRACT.md）、修改材料（REVISE.md）、runs/ 下其他任何文件。

## 一、评审范围（一次审完，不越界不遗漏）

1. 6 个字段取值（research_area / research_question / method_summary /
   training_paradigm / base_model / data_scale）：每个取值是否能在原文中找到直接
   依据、边界判定是否正确（边界条款见 rubric 各字段节：方向 ≠ 问题 ≠ 方法、
   `不训练` vs `[]`、宿主模型 vs 对比模型、训练数据量 vs 评测集规模）。

并指出 rubric 本身是否有歧义。前置分析不在评审范围——你看不到它，也不要去读它。

## 二、判定规则

对每个发现的问题给出 verdict，只能是三者之一：

| verdict | 含义 |
| --- | --- |
| `compliant` | 符合 rubric —— **不要写进 `findings`**：合规字段直接不出现（见第四节） |
| `violation` | 输出违反 rubric —— 建议改执行文档 |
| `rubric_ambiguous` | rubric 本身歧义/冲突/未覆盖 —— 建议改 rubric（需人工批准） |

硬性要求：

- 每一条 violation 必须引用原始输入中的原文作为证据；没有原文依据的指控不要写。
- 引用 rubric 条款编号（如 `4.2`、`5`）便于定位。
- 评审意见必须自包含：修订方只看评审文件本身就能动手，不再读论文与 rubric。为此每条意见都要带全上下文（见第三节字段说明）；引用 rubric 条款时把条款原文一并抄入 `rule_text`；`suggestion` 写成可直接替换的成文措辞，不写「应更具体」这类方向性空话。
- `current` 必须**原样抄录**（2026-09-30 收紧，两批复现后修订）：从 `fields.json` 中把被判违规的字段值逐字抄入（如 `["不训练"]`）。**禁止**叙述性包装、**禁止**概括改写——下游会做 `current ⊆ fields.json` 机器核验，包装与改写一律核验失败、整条评审作废重发。
- **被判违规的字段原值本身为空时**（2026-10-01 新增，由 `findings-acl.1200` 触发：判「`base_model` 不该留空」，而原值就是空字符串）：`current` 写空字符串、**不必**也无法逐字抄录；此时**必须给出 `field` 字段名**，核验改走字段名路径——脚本核对 `fields.json` 里该字段确实为空（`""` / `[]`）才算可核对。没给字段名、或该字段其实非空，同样判核验失败。
- 不要修改抽取输出，不要重新抽一遍，只做判定与建议。

结论必须可证伪。无效结论：「写得不准确」「有点泛」「不太好」。有效结论必须同时给出：被违反的 rubric 条款号 + 条款原文（`rule_text`）、抽取输出中的原值（`current`）、论文原文的逐字引用作证据（`evidence`）。

## 三、输出契约（聚合脚本的前置，错一项整批数据作废）

1. **纯 JSON**：不加 ```json 围栏、不夹任何说明文字，`json.load` 必须直接可解。
2. 顶层固定：`paper_id / perspective（"full"）/ findings / rubric_issues / overall`，结构示意（实际输出不带围栏与注释）：

```json
{
  "paper_id": "从输入中取",
  "perspective": "full",
  "findings": [
    {
      "field": "字段名",
      "verdict": "violation",
      "rule_ref": "rubric 条款号",
      "rule_text": "该条款原文",
      "current": "抽取输出中的原值（原样抄）",
      "evidence": "引用原始输入原文",
      "suggestion": "可直接替换 current 的成文措辞"
    }
  ],
  "rubric_issues": [
    {
      "rule_ref": "rubric 条款号",
      "rule_text": "该条款原文",
      "issue": "歧义/冲突描述（附上引发歧义的具体输出片段）",
      "suggestion": "建议如何修订 rubric（不生效，等人批）"
    }
  ],
  "overall": "pass"
}
```

3. `overall` 取值：`pass`（无 violation）或 `needs_fix`（有 violation 或 rubric_issue）。
4. 每条 violation 带四字段：`rule_text / current / evidence / suggestion`；
   suggestion 必须是可直接替换的成文措辞，禁止方向性空话。
5. `findings` 与 `rubric_issues` 为空时填空数组 `[]`。
6. rubric_issues 写条款歧义/冲突/未覆盖，同样附条款原文与触发歧义的输出片段；
   它们不生效，交人裁。

## 四、不要误判（评审侧的高频错误）

| 错误 | 正确做法 |
|---|---|
| 越权重抽：自己给出「更好取值」 | 你只判合规，无权重写。「换个值更好」不是 violation |
| 把合规字段也列进 `findings` | `findings` 只放 `violation` / `rubric_ambiguous` 条目；6 个字段全合规时 `findings` 写 `[]`（列满 6 条 compliant 是纯冗余，下游只统计非合规项，2026-09-30 batch_014 两篇实测） |
| 拿格式包裹说事 | JSON 围栏有无、json 前后多余文字不属评审范围，解析层容错 |
| 字数卡边缘 | 字数条款统一 ±4 字容差（rubric 条款 0），容差内不判；切词临界差异（分隔符、数字串）不讨论 |
| 把单次偏差当系统性问题 | violation 就事论事记录，是否动手改执行文档由主 agent 按聚类计数决定，不由你判断 |
| 用执行文档的措辞当判据 | 判据只有 rubric；执行文档写法与 rubric 的偏差本身是评审发现，但它属于 rubric_issues / suggestion，不是给抽取输出定罪的依据 |
| 倒装/句式误伤 | 名词化倒装本身不违规（rubric 条款 1）；判语义，不判句式 |

## 五、纪律

- 每篇一次调用内审完全部字段，不要说「其他问题由别的评审员负责」。
- 不确定是否违规时，降级为 rubric_issue（写清歧义在哪），不要硬判 violation。
