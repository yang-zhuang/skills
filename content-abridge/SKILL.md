---
name: content-abridge
description: 按编辑方针把文章（paper / blog / 教程）忠实缩编成中文短版（非摘要）。仅在 content-distill 项目内、由主 agent 编排起草/评审/修订循环时使用。
---

# 缩编（abridgement）

把一篇英文/中文文章按 `references/retention-policy.md` 里的编辑方针缩编成忠实短版：主线全保、支线删净、不改写、不评价。**不是摘要**——摘要由模型自由判断轻重，本 skill 由方针决定删留。

## 什么时候用 / 不用

**用**（全部满足）：

- 存在一个已构造好的任务包 `runs/<task_id>/`（由 `build_task_pack.py` 生成）；
- 主 agent（编排器）派发了起草、评审、修订三种角色之一；
- 产物是 markdown（可含表格、SVG 围栏、图片引用）。

**不用**：

- 只想把文章"总结成几句话"→ 用普通摘要即可，不要走本流水线；
- 没有任务包、只有裸 HTML/裸 markdown → 先让主 agent 跑 `convert.py` + `build_task_pack.py`（见下"脚本速查"），不要直接对裸文件动笔；
- 方针类型判定为 `uncertain` → 终止并报告，不得猜测套用近似策略；
- 需要对作者或其工作做评价、点评、赏析 → 本 skill 明确禁止。

**长文档附加**（source.md ≥ 30,000 字符，`meta.json` 的 mode=long）：起草前先过 trim 阶段——`build_outline.py` 出目录 → trimmer（新角色，见路由表）裁决 → `execute_trim.py` 删除达标；仍超再走二轮简化指令。短文档没有 trim 阶段。

> **预期说明（别把常态当故障）**：30,000 是"达标即停"的机械判据，**不是交付稿的目标长度**。当文章的核心内容本身落在完整保留档（Methods / 公式 / 伪代码 / 提示词）或带大附录时，两轮 trim 之后仍远超 30k 完全正常（ACL paper 实测：106,912 → 83,596 → 成稿 55,580）。剩余压缩由起草阶段按 `simplify-directives.json` 完成，不需要为了让数字降到 30k 去删不可删的内容。

## 目录结构

skill 包（本目录，能力层，保持稳定，只走演化流程才改）：

```
skills/content-abridge/
├── SKILL.md                        # 入口（本文件）：适用边界、角色路由、脚本速查
├── references/                     # 判断层，按角色按需读
│   ├── retention-policy.md         #   编辑方针（唯一删留依据，v6.x，git 版本化）
│   ├── draft.md                    #   起草角色：判断规则 + 输出契约（含长文档简化执行规则）
│   ├── evaluator.md                #   评审角色：维度、三分类、YAML 契约（含压缩力度核对）
│   ├── reviser.md                  #   修订角色：执行标准 + 禁读原文
│   └── trimmer.md                  #   trimmer 角色（长文档专属）：目录裁决规则 + 两轮输出契约
└── scripts/                        # 机制层，全部 Python，agent 只跑不写
    ├── README.md                   #   每个脚本的用法、参数、坑（含 KaTeX 实测附录）
    ├── convert.py                  #   本地 html → md + 图片本地化 + 数学区转义还原
    ├── convert_paddle.py           #   PaddleOCR-VL json → md + 图片本地化（ACL 语料；.0 卷 front matter 自动拒绝）
    ├── repair_math.py              #   回填历史稿：还原数学/代码区被转义的 \_ \*
    ├── build_task_pack.py          #   构造任务包（只组装不删改）+ 方针快照 + 候选引用表
    ├── thresholds.py               #   长文档判定阈值唯一事实来源（build_task_pack 与 execute_trim 同口径）
    ├── build_outline.py            #   长文档目录提取（outline.json：id/chars/figs/首末句/flags）
    ├── execute_trim.py             #   长文档裁决校验 + 整节删除 + manifest（--validate-directives 校验二轮指令）
    ├── extract_refs.py             #   原文外链抽取 → 候选引用表（补生成用）
    ├── classify.py                 #   类型判定（可选自动化；打磨阶段由主 agent 直接判定，端点暂不接）
    ├── validate_review.py          #   评审 YAML schema 校验
    ├── verify_blocks.py            #   围栏块逐字节/去转义/行级三档溯源 + 语言标记检查
    ├── verify_citations.py         #   引用合规校验（铁律 10 的机器检查）
    ├── verify_assets.py            #   图片引用一致性校验（含被删段图片防回流）
    ├── collect_assets.py           #   草稿图片引用物化 + SVG 围栏转文件
    └── aggregate.py                #   defer 意见跨任务频次聚合
```

任务包（`runs/<task_id>/`，数据层，每次运行新生成；角色只读自己需要的部分）：

```
runs/<task_id>/
├── meta.json                 # 类型判定结果、方针版本、mode=short/long
├── state.json                # 循环状态（轮次、stage：ready / trim-delete-pending /
│                             #   trim-simplify-pending）
├── policy/retention-policy.md  # 本次运行的方针快照（执行时一律读这份，版本以首行为准）
├── input/
│   ├── source.md             # 原文 markdown（长文档 trim 后 = 剥离版；全文在 source-full.md）
│   ├── source-full.md        # 【仅长文档】trim 前全文留档（审计回退）
│   ├── outline.json          # 【仅长文档】目录（trimmer 的唯一判断输入）
│   ├── trim-decision.yaml    # 【仅长文档】trimmer 一轮删除裁决
│   ├── trim-manifest.json    # 【仅长文档】trim 执行留痕（每节删/留/暂缓 + 理由）
│   ├── simplify-directives.json  # 【仅长文档】trimmer 二轮简化指令（起草的硬约束）
│   ├── assets/               # 原文图片
│   ├── assets-manifest.json  # 图片原名映射
│   └── refs-candidates.json  # 候选引用表（正文引用只能从中取 URL）
├── draft/                    # 起草产出
├── reviews/review-r<N>.yaml  # 评审产出
├── revised/                  # 修订产出
└── _defer-aggregate.json     # defer 意见聚合（runs 根，跨任务）
```

## 角色路由

本轮扮演哪个角色由任务包状态决定，**只读自己角色对应的文件**：

| 本轮任务 | 读 | 产出 |
|---|---|---|
| 起草初稿 | `references/draft.md` | `draft/draft.md` |
| 评审出意见 | `references/evaluator.md` | `reviews/review-r<N>.yaml` |
| 修订 | `references/reviser.md` | `revised/revised-r<N>.md` + `revised-r<N>-state.yaml` |
| trim 一轮删除裁决（stage=trim-delete-pending） | `references/trimmer.md` + `input/outline.json` + `meta.json` 的 type | `input/trim-decision.yaml` |
| trim 二轮简化裁决（stage=trim-simplify-pending） | `references/trimmer.md` + 删除后的 `input/outline.json` + `meta.json` 的 type | `input/simplify-directives.json` |

判定规则：任务包中没有 `draft/draft.md` → 起草；有稿（draft 或最新 revised）但没有对应轮次的 review → 评审；有 review 但没有对应 revised → 修订；`state.json` stage 为 `trim-delete-pending` / `trim-simplify-pending` → trimmer（**只读 outline 与类型，禁止读 source.md / assets/**——全文物理不在其可见范围是设计边界）。判定不了就报告，不猜。

循环由主 agent 驱动，上限 N=3 轮，无 must-fix 提前终止。

## 机制脚本速查

判断规则在 `references/`，机制操作一律跑脚本（详细用法与坑见 `scripts/README.md`）。Python 用 managed venv：`C:/Users/lenovo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`，工作目录为项目根 `content-distill/`：

```bash
# 预处理：html → md（含数学区转义还原）
python skills/content-abridge/scripts/convert.py <article_dir> <out_dir>

# 构造任务包（只组装不删改；含方针快照 + refs-candidates.json）
python skills/content-abridge/scripts/build_task_pack.py <converted_dir> <runs_dir> <skill_dir> <task_id>

# 长文档 trim（stage=trim-delete-pending / trim-simplify-pending 时）
python skills/content-abridge/scripts/build_outline.py runs/<task_id>/input/source.md runs/<task_id>/input/outline.json
python skills/content-abridge/scripts/execute_trim.py runs/<task_id>                        # 一轮：删 + manifest
python skills/content-abridge/scripts/execute_trim.py runs/<task_id> --validate-directives  # 二轮：只校验简化指令

# 起草/修订产出后的三项机器校验（任一 FAIL 即打回重出）
python skills/content-abridge/scripts/verify_blocks.py runs/<task_id> draft/draft.md
python skills/content-abridge/scripts/verify_citations.py runs/<task_id> draft/draft.md
python skills/content-abridge/scripts/verify_assets.py runs/<task_id> draft/draft.md

# 草稿图片物化 + SVG 围栏转文件（起草产出后、校验前跑）
python skills/content-abridge/scripts/collect_assets.py runs/<task_id> draft/draft.md

# 评审 YAML 校验（不合格直接打回重出）
python skills/content-abridge/scripts/validate_review.py runs/<task_id>/reviews/review-r1.yaml

# 其余：repair_math.py（回填历史稿，接受多个 md 路径）、extract_refs.py、aggregate.py；类型判定 2026-10-06 起由主 agent（多模态 LLM 本体）直接填 meta.json，classify.py 保留为端点自动化选项，暂不接
```

## 所有角色共同遵守

- **编辑方针**是 `references/retention-policy.md`（skill 内文件是编辑源头）。执行时一律读任务包内 `policy/retention-policy.md`——那是 `build_task_pack.py` 复制进来的本次运行快照，版本号以快照首行为准。
- 判断规则只存在于 `references/` 角色文件和方针里；机制性操作由 `scripts/` 完成，你只产出 markdown / yaml 文本。
- 输入文件缺失 → 终止并报告，不要自行补救。
- 禁止对作者或其工作做任何评价；禁止添加原文没有的分析和解释。
- 有两类内容会被**机器按格式校验**（`verify_citations.py`、`verify_blocks.py`），格式写错即打回，必须照 `references/` 里的输出契约写：文末 `## 参考资料` 的 `- [n] 名称 <URL>` 条目与正文 `[n]`；代码块/命令/提示词/JSON 的 ```` ``` ```` 围栏（语言标记要与内容相符，提示词不是 Python 就别标 python）。
- **图像能力纪律**：图的归属、alt 描述、图面核对要求真正"看图"。若执行环境读不到图，**如实标记该维度为 blocked 并上报**，禁止用文本推断冒充图面核对、禁止推定为合规；由编排器另派具备图像能力的角色补核后再结案。
