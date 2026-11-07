---
name: translate-zh
description: EN→ZH 翻译流水线（自包含，整个技能包可分发）。用户给文本 / HTML / URL / PDF / 论文要翻译时，由主 agent 按本入口编排：确定性工作交给 scripts/ 下的脚本（零 token），判断工作交给只读自包含任务包的全新 agent（翻译执行 / 终审评估 / 按块修订），成本超线必须先经用户确认。路径与外部服务配置走 .env.example 模板。触发词：翻译、英译中、论文翻译、arxiv 翻译、ACL 论文、长文分块翻译。
---

# translate-zh · 翻译流水线入口

## 1. 这是什么（目的先行）

把「翻译一篇英文内容」拆成两类工作，用自包含任务包衔接：

- **确定性工作 → 脚本**：token 计量、成本核算、按结构分块、章节剔除、URL 抓取查重。
  代码做，100% 可复现，零 token —— 凡是能枚举的规则绝不写进提示词。
- **判断工作 → agent**：标题分类、术语维护、翻译、交付前的独立评估与修订。
  每次翻译调用都是**全新 agent**，只读自包含任务包（bundle），对其他块一无所知；
  评估 agent 同样全新，且不知道翻译过程（新鲜眼睛，见 references/review-and-revise.md）。

工作根：本 skill 自带 `data/`（术语唯一真相源 + 译文示例），脚本路径全部从脚本位置相对推导
（`scripts/_config.py` 是唯一来源），兼容两种布局，**换机器不用改代码**：

| 布局 | 包的位置 | 工作根（命令在这里执行） |
|---|---|---|
| A 原项目 | `<root>/skills/translate-zh/` | `<root>`（项目根，含 `data/` `outputs/` `.env`） |
| B skills 仓库 | `<repo>/translate-zh/` | 包自己（`<repo>/translate-zh/`，data/ 与 outputs/ 落在包内） |

语料目录 / OCR 凭据等外部配置见 `.env.example`（复制为 `.env` 修改，就近优先；
优先级：系统环境变量 > .env > 默认值）。两种布局下 `data/` 都必须存在，否则 bundle 会直接报错。

部署依赖：Python 3.10+；`pip install requests beautifulsoup4 pypdf`（fetch / pdf2md / ocr_pdfs 用）；
其余脚本只用标准库。PDF 双路径都闭环：born-digital 用 `pdf2md.py`（本地抽文字层），
扫描件用 `ocr_pdfs.py` + `paddle2md.py`（OCR 需自备 PaddleOCR token 或本地服务，见 .env.example）。

## 2. 整体架构：角色、可见内容、产物

流水线里只有四类角色。除主 agent 外，其余每次派工都是**全新 agent**，互相不知道
对方的存在，只通过落盘的**产物**通信：

| 角色 | 全新？ | 它能看到 | 它产出 |
|---|---|---|---|
| **主 agent（编排器 + 工具匠 + 文档演化者）** | 当前会话本身 | 全部编排知识：入口规则、底账、分类表、各执行者回执 | 三类判断，**不产出译文**：① **编排**：分派表、术语裁定、外派与回执裁定、守恒账；② **固化**：同一手工动作出现第二遍 → 固化为脚本（§9 的 check.py 就这么来的）；③ **演化**：评估/修订回执的规则缺口（同一缺口 ≥2 篇）+ 用户否决 → 修 references/* 与 glossary.yaml，先行落盘（用户否决制）。演化权独占，执行/评估/修订 agent 一律不改文档 |
| **执行 agent** | 是，分块时每块一个 | bundle（规则摘录+术语投影+词典+示例+锚点+本块原文）或整翻四件套 | 块译文 `NNN.zh.md` + 回执（临机术语、拿不准的点） |
| **评估 agent** | 是，每篇一个 | 源文 + 译文 + glossary + review-and-revise.md（**不知道翻译过程**） | 评估报告：S-### 七字段建议（或「无硬错误通过」）+ 规则缺口报告 |
| **修订 agent** | 是，按需每块一个 | 修订包（bundle + 本块建议清单） | 整块新译文（覆盖 `NNN.zh.md`）+ 回执（逐条采纳/驳回 + **规则缺口报告**） |

与角色对应的磁盘产物（outputs/ 下），以及谁写出、谁消费：

| 产物 | 谁写出 | 谁消费 |
|---|---|---|
| `<名>.source.md` | fetch.py / preprocess.py | 底账、翻译的唯一源（只读，绝不回写） |
| `<名>.clean.md` | sections.py strip | split / 整翻的输入 |
| `chunks-XX/NNN.md` + `manifest.json` | chunk.py split | bundle 的原料 |
| `chunks-XX/NNN.task.md` | chunk.py bundle（术语投影+词典+锚点自动组装） | 执行 agent 的唯一读物 |
| `chunks-XX/NNN.zh.md` | 执行 / 修订 agent | 下一块锚点、join 的原料 |
| `chunks-XX/lexicon.md` | 主 agent（裁定执行者回执后落盘） | 下一块 bundle 自动携带 |
| `<名>.zh.md` | chunk.py join | 评估 agent、最终交付（文末「翻译说明」附录） |

## 3. 什么时候用 / 不用

**用**（全部满足）：

- 用户给了要翻译的内容：手抄文本 / HTML 文件 / 消息里的 URL / 已抓取的论文；
- 主 agent 在本项目内编排，能执行 `scripts/` 下的脚本；
- 用户在意译文质量与术语一致性，不是"随便总结一下"。

**不用**：

- 只想把文章总结成几句话 → 普通摘要即可；
- 非 EN→ZH；
- 工作根下没有 `data/`（bundle 会因为找不到术语表直接报错，不会静默降级）。

## 4. 执行顺序（总览）

| # | 步骤 | 用什么 | 细节在哪 |
|---|---|---|---|
| 0 | **成本闸门**：开翻前必跑 cost 并展示；预估 >30000 须用户对这份估算明确确认 | `chunk.py cost` | references/chunking-and-cost.md §2 |
| 1 | 输入归一成 markdown（文本 / HTML / URL 抓取查重） | `fetch.py` `preprocess.py` | references/input-preprocessing.md |
| 2 | 章节底账：正则找标题 + 各节 token（零 token），可枚举剔除类直接标「跳过」 | `sections.py list` | references/paper-disposition.md |
| 3 | AI 只做标题分类（~1k token，只看标题清单不看正文）：主 agent 先行给分派表，用户否决制 | — | references/paper-disposition.md + chunking-and-cost.md §3 |
| 4 | 翻译：≤5000 整翻、>5000 分块——**都外派全新执行 agent，主 agent 永远不亲自翻译**（分块走 bundle，整翻走四件套文件清单：execution-rules + glossary + examples + 全文） | `chunk.py split/bundle` | references/chunking-and-cost.md §1+§8 |
| 5 | 合并 → 清单统一 | `chunk.py join/reconcile` | references/chunking-and-cost.md §6 |
| 6 | **独立评估**：全新 agent 只面对源文+译文，只报硬错误，建议七字段自包含；评估→修订构成**有界循环（封顶 2 轮评估）** | — | references/review-and-revise.md §1.3 |
| 7 | **修订 → 验收**：确定性修改块文件（grep 兜底）；判断性修按块外派修订 agent → re-join；被改 >3 处 → 该块重新外派评估复核 | `chunk.py bundle/join` | references/review-and-revise.md §1.3+§2 |
| 8 | 守恒校验 → 交付 | 脚本计数对比 | references/chunking-and-cost.md §9 |

## 5. 人工反馈的定位：默认自主，反馈是加速通道

**没有人工反馈，全流程也必须能跑完出稿** —— 等人点头才动 = 这套流程没有存在
价值。流程是自主的：翻译 → 评估 → 修订 → 验收一路走完。评估→修订是**有界
循环**：封顶 2 轮评估、只复核被改块（references/review-and-revise.md §1.3）；
无条件全文多轮循环已否决（references/design-principles.md §7）。人工反馈
只有四个合法入口：

| 反馈点 | 触发 | 形式 |
|---|---|---|
| 成本闸门 | 估算 >30000 token | 必须明确确认，未确认不开翻 |
| 分派表 | 每篇开翻前 | 主 agent 先行给出，**否决制**（不反对就执行） |
| 术语表 | 主 agent 落盘后 | 同为否决制；一句话永久生效 → 写进 data/glossary.yaml |
| 交付后 | 用户读成品 | 指哪句改哪句，单点重翻，不重跑流程 |

## 6. 分层文档（按需读，不要全量加载）

下表路径均相对 skill 包根（布局 A 为 `skills/translate-zh/`，布局 B 为 `translate-zh/`）。

| 文件 | 管什么 | 什么时候读 |
|---|---|---|
| references/input-preprocessing.md | 输入形态识别与归一（文本 / HTML / URL） | 拿到新输入时 |
| references/paper-disposition.md | 论文 / 长文档按节取舍（剔除类、内容取舍表） | 输入是论文或长文档 |
| references/chunking-and-cost.md | 分块、成本闸门、翻译模式、bundle 隔离、词典、合并 | 源 >5000 token，或开翻前 |
| references/execution-rules.md | 翻译判断准则 + 硬边界 + 输出格式 | **每个翻译执行者必读**（bundle 中指定） |
| references/review-and-revise.md | 评估与修订（硬错误清单、七字段建议、修订包组装、评估成本、**规则缺口闭环**） | **评估 / 修订派工时必读**（评估 agent 指定读） |
| references/design-principles.md | 设计原则与文档演化权限（谁能改哪份、已否决设计、升级路径） | 改文档前、或对某个"为什么"有疑问时 |

## 7. 脚本速查

```bash
# 工作根下执行（两种布局见 §1）
python skills/translate-zh/scripts/chunk.py     count <md>                  # token 数，判整翻还是分块
python skills/translate-zh/scripts/chunk.py     cost  <md> [--factor F]     # 成本估算，开翻前必跑
python skills/translate-zh/scripts/chunk.py     split <md> --out-dir <dir>  # 按结构切块 + manifest.json
python skills/translate-zh/scripts/chunk.py     bundle <dir> --index N      # 第 N 块自包含任务包
python skills/translate-zh/scripts/chunk.py     join  <dir> -o final.zh.md  # 全部翻完再合并
python skills/translate-zh/scripts/chunk.py     reconcile final.zh.md       # 统一清单项译法
python skills/translate-zh/scripts/chunk.py     conservation <源.md> <译.md> # 交付前守恒校验（失败退出码 1）
python skills/translate-zh/scripts/sections.py  list|strip|detect|test      # 论文按节取舍
python skills/translate-zh/scripts/fetch.py     plan|fetch|test             # 消息 URL → 查重 → 下载
python skills/translate-zh/scripts/preprocess.py <html> [out] [base_url]    # HTML → markdown
python skills/translate-zh/scripts/scan_corpus.py <语料目录> [输出]          # 语料扫术语候选
python skills/translate-zh/scripts/pdf2md.py    <pdf或目录> [输出]           # born-digital PDF → md（pypdf 文字层）
python skills/translate-zh/scripts/paddle2md.py <json或目录> [输出]          # PaddleOCR json → md（扫描件收尾）
python skills/translate-zh/scripts/ocr_pdfs.py  --input <目录> [--mode local] # 批量 OCR（需 OCR_TOKEN，见 .env.example）
```

## 8. 数据文件（工作根 data/，布局 B 在包内自带）

| 文件 | 角色 |
|---|---|
| data/glossary.yaml | 术语唯一真相源（keep / expand / translate / pending / gloss + units），bundle 自动投影本块命中的词条 |
| data/examples.md | 好坏译文对照示例，随 bundle 下发 |
| data/candidates.md | scan_corpus.py 扫出的未确认候选 |

术语维护权限（主 agent 先行、用户否决制、执行 agent 只读）见 references/execution-rules.md。

**文档演化权限**（谁能改哪份、规则缺口 ≥2 篇才动的闭环）：见
references/design-principles.md §5 + references/review-and-revise.md §5。
要点：演化权**独占在主 agent** —— 执行/评估/修订 agent 只产出译文/报告 +
回执（含规则缺口报告），绝不改文档；用户一句话可改任何文档，最高优先。

## 9. 已知待建

`check.py` 硬检查（keep 违规 / 单位换算 / 链接失效 / 代码块被改 / **结构守恒计数**）——
5–10 篇之后再建，没见过真实错误就写检查等于凭空编规则。结构守恒计数（标题/链接/图片）
已两次实测有效，是 check.py 的第一项。
