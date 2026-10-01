---
name: paper-screening-pipeline
description: 从已 OCR 的论文池按批次抽样，每篇由「执行 agent 抽 6 个字段 → 单评审 agent 全字段评审」两次调用完成，评审意见聚类后按三级分级决定改不改执行文档，改完换新论文复测——把整条闭环的作业方式、权限边界与终止条件固定下来，换一批论文、换一个项目都能照跑。适用于用 LLM 多 agent 对论文做批量字段抽取与质量评审的场景（研究领域盘点、文献筛选、训练范式归类）。当用户说"筛论文""批量抽取论文字段""评审抽取结果""跑一批论文试试""闭环跑通没有""这条标准要不要改""改完标准要不要重测"时使用。关键词：论文初筛、字段抽取、多 agent 闭环、执行/评审/修改 agent、三级分级、定向回归、跨批复现、rubric 迭代、指纹锁定。实测于 ACL Anthology 2026 论文池（29 批 149 篇，含 3 轮 rubric 人批裁决 + 4 次定向回归，改后回归 violation 全 0；2026-10-01 起按内容类型分层抽样，8 型全覆盖）。
agent_created: true
---

# 论文初筛闭环（agent 版）

从已 OCR 的论文池里按批次抽样，每篇由「执行 agent 抽 6 个字段 → 单评审 agent 全字段评审」
两个调用完成，评审意见聚类后按三级分级决定要不要改执行文档，改完换新论文复测。
这个 skill 把整条闭环的作业方式、权限边界和终止条件固定下来，换一批论文、换一个项目都能照跑。

**核心纪律一：凡是确定性的动作，一律是 `python xxx.py 参数`。**
抽样建批、组装输入、聚合评审 JSON、算指标、评审引用核验、修改稿契约校验都是程序；
agent 只干三件事：**抽取**（执行 agent）、**评审**（评估 agent）、
**按三级纪律改执行文档**（修改 agent）。

由此推出**主 agent 少说话**：它只在两处出声——派工（一次一个角色）与念脚本结论。
任何复核、对比、统计、抽查都不许用临时内联代码或自然语言叙述代替：
**能落成 `python xxx.py` 的一律先落成脚本**，再跑它、念它的输出。理由不是风格，
是可复现——内联代码只活在当前上下文里，下一批要对账时它已经不存在了。

**核心纪律二：每篇论文只有 2 次调用。** 1 次抽取 + 1 次单评审（一次审完全部字段）。
评审只看 6 字段 JSON（`fields.json`），**不看前置分析**——前置分析是执行端的推理过程，
评审看了会被它的判断锚定，失去独立性。不要拆多视角评审——每加一个视角就多一遍
rubric 与论文全文的重复阅读，而视角隔离换不来判定质量，只按倍数放大成本与耗时。

**核心纪律三：不是所有评审意见都要改。** 同一问题跨篇或跨批复现 ≥2 次才动手；
单次只记录等复现；rubric 自身歧义走人裁，禁止改执行文档把歧义糊住。
分级判据见 `references/REVISE.md`。

## 一、规则材料随本 skill 自带，workspace 只放数据

| 材料 | 在哪 | 说明 |
|---|---|---|
| rubric（字段规范） | `references/RUBRIC.md` | 唯一判定标准；人批修改只发生在这里 |
| 执行/评审/修改作业文档 | `references/{EXTRACT,EVALUATION,REVISE}.md` | 就是发给对应子 agent 的指令正文，闭环迭代的对象 |
| 7 个脚本 | `scripts/`（复制进 workspace 根目录后运行） | 纯标准库，Python 3.10+ |
| 已 OCR 的论文池 | `<workspace>/ocr_out/`（`screen_papers.py` 认的目录结构） | 唯一的 workspace 硬前置，缺了抽不到论文 |

脚本通过 `screen_papers.py` 顶部的 `REF_DIR` 常量定位本包 `references/`（默认
`G:/skills/paper-screening-pipeline/references`，换机器用环境变量 `SCREEN_REF_DIR` 覆盖）。

## 二、工作区结构（**固定，不要自己发明**）

```
<workspace>/
├─ screen_papers.py 等 7 个脚本        从本包 scripts/ 复制来的运行副本
├─ runs/
│   ├─ batch_NNN/
│   │   ├─ manifest.json              锁 rubric/执行文档的 sha1 —— 版本号会记错，指纹不会
│   │   ├─ inputs/                    每篇的组装输入（只读）
│   │   ├─ extracts/                  执行 agent 产出（前置分析 + JSON）+ <pid>.fields.json（collect_extracts 生成的评审专用纯 JSON）
│   │   ├─ reviews/                   评审 agent 产出（纯 JSON，可 json.load）
│   │   └─ metrics.json / merged_highfreq.json / cross_batch.json   聚类脚本产出（跨批复现判据）
│   └─ prompt_snapshots/              每次改动前的文档快照（回滚靠它，不靠记忆）
└─ ocr_out/                            论文池（只读）
```

规则与文档不在 workspace：闭环直接修改本包 `references/EXTRACT.md`，改前快照进
workspace 的 `runs/prompt_snapshots/`。历史批次 manifest 锁的是建批当时的
rubric / 执行文档指纹（最早几批锁 `screen_rubric.md` 的 `8772d5c0d3ef`，对应
2026-09-29 之前的 rubric）；此后经多轮人批修订——每次修订的裁决与去向记在 workspace 的
`runs/ruling_*.md`，改完的回归验证记在同一目录的 `runs/regression_*.md`，新批的泛化检验记在
`runs/generalization_*.md`。新批 manifest 自动锁新指纹，历史对账按各自建批时的指纹来。

## 三、闭环五步：每步一条命令或一次派工

在 `<workspace>` 根目录执行：

```bash
# 1 建批（纯代码）：抽 n 篇新论文，自动排除历史批次已用过的 paper_id，
#   manifest 锁当前 rubric/执行文档的 sha1
python prepare_batch.py --seed 1009 --n 6

# 1' 分层建批（2026-10-01 新增）：按内容类型给固定配额，保证低频型不被饿死。
#    纯随机抽样在 1500+ 篇的池上会公平地饿死低频型——综述只占 1.3%，
#    连跑 21 批随机抽样一篇综述都没测到过。先跑一次分类器建分桶：
python classify_papers.py                          # 写 runs/paper_types.jsonl
python prepare_batch.py --seed 1016 --strata 1      # 8 型各 1 篇
python prepare_batch.py --seed 1015 --strata 2 --types survey,theory,nonstandard  # 给低频型加量

# 2 抽取：每篇派一个全新执行 agent
# 3 评审：每篇派一个全新评审 agent
# 4 聚类 + 评审引用核验 + 跨批复现判定（纯代码）：
#   核验不过的评审不参与聚类，退出码 1 列出要重发的篇
python cluster_reviews.py --batch batch_009

# 5 按三级分级决定：本批高频与跨批复现清单由第 4 步一并给出；
#   无「立即修改」级 → 直接回 1 跑下一批；
#   有 → 派修改 agent（只读 RUBRIC.md + EXTRACT.md + REVISE.md + 过滤后清单），
#        产出 draft 后过闸门并落地（不过闸门绝不落地，也不会留下快照）
python check_draft.py --draft runs/batch_009/extract_vN_draft.md --apply
```

**改完 rubric 之后怎么验（2026-09-30 新增）**：跑新批看不出差别——条款越界类触发率极低
（14 批约 75 篇里只触发 7 次），新批大概率一篇都不命中。正确做法是**定向回归**：把在这条上
被判过的论文重新组批，两段都重跑。

```bash
# 定向回归：--papers 跳过随机抽样与历史排除，只建指定论文的批
python prepare_batch.py --papers 2026.acl-srw.54,2026.acl-long.1252 --seed 0
```

**执行端必须一起重跑，不能只重跑评审**：新标准下「应该改填什么」本身会变——旧输出在新标准
下可能仍是 violation，只是应改的值不同（实测：`acl-srw.54` 旧填 `[]`，新边界句下仍是
violation，应改 `不训练`）。拿新 rubric 喂旧 `fields.json` 看不到这一层。**判读标准要在开跑前
写死**（哪几篇预期转 pass、哪几篇预期仍 violation、哪几篇不受影响），跑完再找理由等于自证。

**rubric 改完，`EXTRACT.md` 的同步也走修改 agent**（2026-09-30 实测）：人裁改的是 `RUBRIC.md`，
复述同一规则的 `EXTRACT.md` 必须同步，否则执行文档与标准打架（`REVISE.md` 护栏 4「偷偷立法」
的反向版）。这条同步**不要手工 Edit / cp**——按第 5 步派修改 agent 产出 draft、过闸门落地：
`check_draft.py --apply` 会一并完成快照与覆盖，手工改会绕过闸门、漏掉快照。

**并发实践（2026-09-30 实测）**：两个 agent 环节彼此独立，可并发派，但一次派 6 个会触发
429 限流（6 并发实测 2 成功 4 失败）——按 2~3 个一波派，失败的直接重发新 agent 即可
（已落盘的产出不受影响，重发前先 `ls + wc -c` 看缺哪篇，只补缺的）。

三步纯代码加一道闸门（建批 / 聚类+核验 / 契约校验）各约 1 秒；两个 agent 环节决定整批耗时。

## 四、子 agent 怎么派、隔离怎么给

**交接一律走文件。** 主 agent 只读回报摘要（文件路径 + 字节数 + 条数），不把评审
JSON 全文搬进自己的上下文——多批迭代时主 agent 会先爆。

**评审接收后的机器核验**（2026-09-30 新增，两批复现后定稿）：核验已内置在聚类脚本里，
跑 `python cluster_reviews.py --batch <批>` 时一并完成——每条 violation 的 `current` 必须能在
本篇 `extracts/<pid>.fields.json` 中逐字找到（多段用「；」并列时每段都要能找到）；`evidence`
中「」引号内的片段必须能在本篇 inputs 中逐字找到。**核验不过的评审不参与聚类**，脚本以
退出码 1 列出文件名，按纪律重发那几篇评审 agent（评审端的高发残留是包装式/概括式 current，
纯靠肉眼查不全）。

| 角色 | 必读（三样/四样，没有别的） | 严禁读 | 写 |
|---|---|---|---|
| 执行 agent | `references/RUBRIC.md`、`references/EXTRACT.md`、本篇 `inputs/` | `EVALUATION.md`、`REVISE.md`、`runs/` 其他一切 | 本篇 `extracts/<pid>.md` |
| 评审 agent | `references/RUBRIC.md`、`references/EVALUATION.md`、本篇 inputs + `extracts/<pid>.fields.json` | 本篇 `<pid>.md`（前置分析是执行端推理，看了会被锚定）、`EXTRACT.md`（判 rubric 合规，不判文档写法）、`REVISE.md`、runs/ 其他 | 本篇 `reviews/<pid>.full.json` |
| 修改 agent | `references/RUBRIC.md`、`references/EXTRACT.md`、`references/REVISE.md`、过滤后建议清单 | 任何论文原文（清单是自包含的） | `extract_vN_draft.md` + changes |

隔离的两个理由，改之前先想清楚：

1. 执行不读评审材料：闭环隔离，执行端只对 rubric 负责，评审端才能独立发现问题。
2. 评审不读执行文档：判的是「输出是否符合 rubric」，不是「是否符合执行文档的写法」——
   否则评审会跟着文档的措辞走，永远抓不到文档本身的偏差。
3. 评审不读前置分析：前置分析是执行端的推理与辩护，评审看了会被它的判断锚定
   （比如执行端把被测模型错判成宿主模型并给了理由，评审顺着理由就放行了）。
   评审只对 fields.json 的字段值独立作答——执行端推理对不对，由 JSON 取值本身
   是否符合 rubric 来检验。

三份作业文档各自**自包含**：作业所依赖的规则全文写在文档内，条款号（rubric 条款 N）
只作出处标注，读者手上没有 rubric 也能干活。代价是多一处要维护的副本——rubric 修订
涉及这些规则时，主 agent 必须同步更新对应作业文档；字段语义的权威仍是 rubric，
冲突时以 rubric 为准。SKILL.md 正文同理：引用其他文档时必须写明「详见 X」，
不写明的引用视为缺失。

**每个子 agent 都用全新的**（不 resume）——防上下文污染。网络失败（如 502）重发一个
新 agent 即可，已写文件不受影响。

## 五、评审输出契约（自包含，缺一字段即不合格）

每条 violation 必须带四个字段，修改 agent 只看评审文件就能动手、不必重读论文：

```json
{
  "verdict": "violation",
  "rule_text":  "被违反的 rubric 条款原文",
  "current":    "抽取输出中被判违规的原值",
  "evidence":   "论文原文证据（逐字引用）",
  "suggestion": "可直接替换的成文措辞，禁止方向性空话"
}
```

评审文件必须是纯 JSON（无 ```json 围栏），`json.load` 直接可解——这是聚合脚本的前置。

## 六、三级分级：什么意见改、什么不改

详见 `references/REVISE.md`（修改 agent 必读，主 agent 过滤意见时同一张表），一句话版：

- **立即修改**：契约类违规（键名/数组/代码块/两步顺序，机器可查）；同批跨 ≥2 篇或跨批复现。
  跨批的机器判据见第 4 步的 `cross_batch.json`——按（字段/条款, 条款集合）对齐历史各批
  `merged.json`，命中 ≥2 批即跨批复现（**对齐用条款，不用 kind**：kind 是关键词启发式，
  兜底值「其他」会把「引文引错层」和「编造方向词」混成一个家族）。
- **暂缓记录**：单批单篇的 violation 与单发 rubric_issue——任何一批复现即升级。
- **不修改**：评审越权重抽值；无条款依据的措辞偏好；rubric 歧义（走人裁，这是铁律——
  改执行文档掩盖歧义 = 把评估端和执行端一起糊住）。

修改后必须：快照旧版进 `runs/prompt_snapshots/` → 契约校验（6 键、两步顺序、数组、
`extract_json` 实测可解析）→ **换新论文复测**（`--no-exclude` 禁止用于常规批次，
用提出建议的那批数据验证修改 = 过拟合）。

## 七、终止与收敛（三条任一命中就停）

1. 连续两批无新增重复主题、无契约违规 → 执行文档收敛，闭环进入纯跑稳态；
2. rubric_issue 聚集且全是歧义类 → 停下交人裁（不要无限派修改 agent）；
3. 同一 violation 家族三轮改不掉 → `stuck`，停下分析是不是 rubric 本身的问题。

## 八、失败模式速查

| 现象 | 真正的原因 |
|---|---|
| 评审文件 `json.load` 失败 | 评审 agent 加了 ```json 围栏或夹了说明文字——重发一个 agent，提示「纯 JSON」 |
| 抽取 JSON 键缺失/顺序错 | 执行文档的契约段被修改改坏——回滚 `runs/prompt_snapshots/` 上一版再查变更说明 |
| violation 原地打转三轮 | 大概率不是执行端问题：查该条款在 rubric 里是否自相矛盾（禁令与豁免并存、两句话说反） |
| 修改后指标没改善 | 用 `--no-exclude` 在旧批复测过 = 过拟合自测；正确做法是换新 seed 的新批复测 |
| manifest 里 sha1 与当前文件对不上 | 文档被改了但没重建批——manifest 锁的是建批时的版本，跨批对比必须先对指纹 |
| 同一批跑出两套结果 | 复用了历史 inputs。每批必须新 seed 新抽样（防过拟合硬闸在 `prepare_batch.py` 里） |
| agent 回报「已写入」但文件不存在 | 不信回报，`ls + wc -c` 实核——agent 会话可能超时中断 |
| agent 正常回报但压根没调用 Write（产出只在回复里） | 会话未中断也会发生；派工话术末尾必须带硬性要求「必须实际调用 Write 落盘，不能只在回复里给出内容」 |
| 修改后的执行文档出现「如前所述/第一部分」类指代 | 指代指向错误是修改 agent 的高发残留，`check_draft.py` 只报 WARN 不出 FAIL（无法自动判对错），必须人工复核指代表述 |
| `check_draft.py` 报 FAIL | 修改 agent 改坏了下游要解析的东西（键名/代码块/两步顺序）——把 FAIL 明细原样回给它重做，**不许人工修补 draft** |
| `cluster_reviews.py` 报「引用核验未通过」 | 评审的 `current`/`evidence` 不是原样抄录——重发那几篇评审 agent；不合格的评审不参与聚类 |
| `cluster_reviews.py` 报「current 为空」 | 被判违规的字段原值本就是空字符串，逐字比对无从下手——让评审补 `field` 字段名，核验改走字段名路径（`EVALUATION.md` 已写明） |
| 改完 rubric 跑新批看不出改善 | 条款越界类触发率极低，新批抽不到目标篇——改用 `prepare_batch.py --papers` 定向回归，执行端与评审端都重跑（见第三节） |

## 九、`scripts/` 里有什么

7 个 `.py` 全部纯标准库（下表前 6 个是 CLI，最后 1 个是被 import 的库）：

| 文件 | 怎么用 | 什么时候用到 |
|---|---|---|
| `prepare_batch.py` | CLI | 建批：抽样（排除历史）、组装输入、锁 sha1；`--papers` 定向回归（只跑指定论文，见第三节）；`--strata N` 按内容类型分层抽样（配合 `--types` 给低频型加量） |
| `classify_papers.py` | CLI | 论文内容类型分类器（确定性规则，不调用 LLM）：按 venue + 标题 + 摘要 + 章节标题把全池分成 8 型，写 `runs/paper_types.jsonl`，供 `--strata` 分桶。`--pid` 单篇诊断，`--types` 按型列举 |
| `cluster_reviews.py` | CLI | 聚类评审 JSON（指标 + 高频分组 + **跨批复现判定**），**并内置评审引用核验**；核验不过退出码 1 |
| `collect_extracts.py` | CLI | 校验/汇总抽取产出，并生成评审专用的 `<pid>.fields.json` |
| `check_draft.py` | CLI | 修改稿契约校验（8 项闸门）；`--apply` 过闸门即落地（快照旧版 + 覆盖执行文档）；退出码非 0 绝不落地 |
| `orchestrate.py` | CLI（可选） | API 直调版编排（跳过 agent，需 OpenAI 兼容 endpoint）；`--dry-run` 可单测 prompt 组装 |
| `screen_papers.py` | 库 | 被 `prepare_batch` / `collect_extracts` / `orchestrate` / `check_draft` import（输入组装、`load_prompt`、`extract_json` 解析器） |

`scripts/BASE.txt` 是 7 个脚本的 sha256 指纹（`sha256sum -c BASE.txt` 可实测校验）。**发行版
`scripts/` 是只读基线**：脚本的演进发生在 workspace，确认稳定后回灌本包并重算指纹。

## 十、参考文档（按角色分发，按需读）

| 文件 | 谁读 | 什么时候读 |
|---|---|---|
| `references/RUBRIC.md` | 全部环节 + 人 | 执行/评审 agent 随派工必读；人批 rubric 修订在这里 |
| `references/EXTRACT.md` | 执行 agent（闭环迭代对象） | 派工时随 RUBRIC.md + inputs 一起给 |
| `references/EVALUATION.md` | 评估 agent | 派工时随 RUBRIC.md + inputs + fields.json 一起给（不给 extract 原文） |
| `references/REVISE.md` | 修改 agent（主 agent 过滤意见同用） | 派修改前，附过滤后清单 |
| `references/LESSONS.md` | 主 agent | 改 rubric/执行文档之前先翻：这类问题以前踩过没有（含 base 指纹） |
| `references/ruling_*.md`、`references/regression_*.md`、`references/generalization_*.md`、`references/coverage_*.md` | 人 / 主 agent | 人批裁决的落地记录、改后回归核对表、新批泛化检验报告、内容类型覆盖测试报告。改 rubric 前先翻：这条以前裁过没有；跑新批前先翻泛化报告：这类问题跨批复现没有；怀疑某类论文测漏了先翻覆盖报告：按类型看 coverage。权威副本在包内，workspace 的 `runs/` 下是运行期副本（同 `BASE.txt` 模式） |
