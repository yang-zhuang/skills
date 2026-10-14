---
name: wx-article-forge
description: 把任意文档转成微信公众号可粘贴的排版 HTML 并发布。覆盖意图判断、改稿、公式/渲染、视觉/语义评估、修改循环、封面生成、元数据与草稿箱提交。触发词：公众号文章、转微信、公众号排版、发公众号、md转公众号。
---

# 何时用本 skill

用户给了文档（md/文本）并希望：转成公众号文章、排版、评估排版效果、发到公众号（草稿箱/发布）。
一个 skill 覆盖全流程；按用户实际意图执行对应阶段，不必每次跑完全程。

# 代码结构（先读这里，避免探索）

```
scripts/
  normalize.py        语法审计 + 内容清单（字数/图片/代码块数）。CLI: <in.md> [out.md] [--check]
  formula_replace.py  整篇 md 公式识别并替换为图片引用。CLI: <in.md> -o <out.md> --png-dir <dir>
  formula_to_png.py   单个公式转 PNG（调试用，整篇用 formula_replace）。CLI: "<latex>" -o <out.png>
  typeset.py          渲染+清洗+预览一站式（预览页总是生成）。CLI: <in.md> -o <out.html> [--theme x.json]
  render.py           markdown → 内联样式 HTML（typeset 内部调用，一般不直接跑）
  wechat_guard.py     HTML → 微信安全 HTML（typeset 内部调用）
  wechat_rules.py     微信限制常量（被引用，无 CLI）
  evaluate.py         真实渲染 + DOM 断言 + 375px 分页截图。CLI: <in.html> [--raw x.html] [--out <dir>]
  triage.py           评估意见分档记账。CLI: <issues.json> --article <名> [--mark <指纹> --status <档>]
  make_cover.py       代码生成封面。CLI: --title "..." [--subtitle ...] -o cover.jpg
  upload_images.py    正文图批量上传为 mmbiz 链接并改写 html。CLI: <built.html> [--dry-run]
  ship.py             发布预演与提交。CLI: <in.html> --title "..." --cover x.jpg [--dry-run|--check]
  wechat_api.py       微信 API 封装 + 凭据自检。CLI（无参数跑自检）
  theme_lint.py       主题合规 + 历史查重。CLI: <theme.json>
  selftest.py         36 项自检（改代码后回归用）

references/
  briefings/          子 agent 作业手册（派工时整篇交付，见下）
    author.md       改稿
    typesetter.md   渲染（含公式/图片就位）
    evaluator.md    评估（含看图铁律）
    fixer.md        修改（含三档决策）
    cover.md        封面
    publisher.md    元数据与发布（含确认门）
  visual-design-brief.md  视觉设计基准：组件库/五步流/差异化铁律/原文对照表（改稿与渲染的共同依据）
  visual-designer-original-prompt.md  用户原始 prompt 存档（含案例 A 完整 HTML，对照溯源用）
  wechat-constraints.md   微信兼容性全量规则（渲染前对照）
  unsupported-syntax.md   markdown 里必须先改写的语法（改稿时读）
  checks.md               评估检查项与阈值依据（评估时读）
  image-specs.md          图片/封面规格与上传通道（封面、插图上传时读）
  publish-errors.md       微信错误码对照（发布失败时读）

assets/themes/        主题 json + registry.json 查重登记
assets/fixtures/      测试样本
work/                 运行时产物（协议见下），不在 skill 知识包内
```

# 流水线总览

| 阶段 | 执行者 | 输入 → 输出 | 干什么 | 作业手册 |
|---|---|---|---|---|
| 1 意图判断 | 主 agent | 用户消息 → 决策 | 判断走全流程还是直达发布 | 本文「阶段一」 |
| 2 改稿 | 改稿子 agent | 原文 → `1_normalized.md` + `1_baseline.json` | 规范化 md，不删内容 | `references/briefings/author.md` |
| 3 渲染 | 渲染子 agent | 1_normalized.md + 1_baseline.json（tone 定调）→ `2_built.html` | 定调选主题、公式替换、渲染、清洗 | `references/briefings/typesetter.md` |
| 4 评估 | 评估子 agent | 2_built + 基线 → `3_eval/` | 跑评估脚本 + 读图判美 | `references/briefings/evaluator.md` |
| 5 修改 | 修改子 agent | 3_eval → 改 md + 账本 | 意见三档分账，改后回阶段 4 | `references/briefings/fixer.md` |
| 6 封面 | 封面子 agent | 标题 → `5_cover.jpg` | 代码生成，满意后再议 AI 图 | `references/briefings/cover.md` |
| 7 发布 | 发布子 agent | 2_built + 封面 → 草稿箱 | 元数据 + dry-run + 确认门 | `references/briefings/publisher.md` |

阶段 4 ↔ 5 循环，≤3 轮。**每个子 agent 的详细角色、边界、标准、示例都在它自己的 briefing 里**，
本文只放索引，避免两处维护导致漂移。

# 派工协议（主 agent 必读）

**主 agent 只做三件事：**
1. **意图判断**（阶段一）；
2. **派工**：开子 agent，prompt 只含三样——**对应 briefing 文件全文**、
   输入/输出路径、该阶段应读的 references（briefing 里已列清单）；
3. **检查断点**：`ls work/<篇名>/` 看哪些产物已存在，从缺失的最早阶段继续。

**为什么是 briefing 全文而不是阶段摘要**：子 agent 是全新的、零上下文的。
它需要的不是"你的任务是把 md 变成标准格式"，而是"输入是什么、共用目录在哪、
失败报给谁、做完交什么格式"。这些只有 briefing 说得清。

**上下文边界（严格遵守）**：只给该阶段的 briefing，**不给其他阶段的 briefing，不给全流程背景**。
改稿 agent 不知道评估怎么做，评估 agent 不知道发布约束。上下文越纯，判断越稳，token 越省。
子 agent 的结论只回传一页（见各 briefing 第 10 节的回报模板），脚本长输出留在 `work/` 里。

**通信只有文件，没有对话**：`work/<篇名>/` 下固定文件名 = 阶段完成信号。
`work/decisions.json` 是跨文章共享的修改账本（triage.py 维护，勿手改）。
跨阶段信息（如改稿产出的调性 `tone`）已写在产物文件里，派工时**给路径即可，不要口头转述**——
转述必然带上你的全局视角，把别的阶段的知识漏给子 agent。

**循环终止**：`3_eval/eval-report.json` verdict=PASS 且账本无 fix_now 未决项 → 进封面/发布。

# 阶段一：意图判断（主 agent 自己做，不委派）

信号齐全 = 同时满足三条，可跳过改稿直达发布链：
1. 明确发布指令（"发到公众号""提交草稿"）；
2. 内容是最终稿：无 TODO/占位/"待补充"，图片引用齐备；
3. 用户不在讨论措辞修改。

任一条不满足 → 走全流程。拿不准就问一句，不要替用户决定。

# 阶段二至七（派工程序见上）

| 阶段 | 派工时给 briefing | 完成信号 | 关键红线 |
|---|---|---|---|
| 2 改稿 | `author.md` | `1_normalized.md` + `1_baseline.json`（含 `tone`/`mood_keywords`）落盘且 normalize 复跑 ok | 内容保真：不删内容，超预算只压排版并告知用户 |
| 3 渲染 | `typesetter.md` | `2_built.html` + `2_built.preview.html` 产出且清洗 ok=True | 禁止手改 html（重渲染会覆盖）；图片须与 html 同目录 |
| 4 评估 | `evaluator.md` | `3_eval/issues.json` 落盘 | 必须真读分页图 `p1/p2…`，**严禁读 full.png**，读不了要明说 |
| 5 修改 | `fixer.md` | 账本表态完成 + 改动已落盘 | 禁止改 `.py`；每档都要有理由；循环 ≤3 轮 |
| 6 封面 | `cover.md` | `5_cover.jpg` 且规格达标 | JPG / 宽≥900 / 2.35:1 / ≤2MB；用户满意后才问 AI 图 |
| 7 发布 | `publisher.md` | dry-run 通过 + 用户同意 → 草稿箱拿到 media_id | **未得明确同意不得调写接口**；摘要≤120 字；图片必须先转 mmbiz |

# 微信硬约束速查（全量见 references/wechat-constraints.md）

- 标题层级上限 3 级，h4-h6 渲染器自动降级并告警，绝不静默
- `<style>`/class 被剥：样式必须全内联；容器用 `<section>`（div 样式会被清）
- position/z-index/transform/animation 全部失效；script/iframe/style 整节点剔除
- 正文图仅限 mmbiz.qpic.cn；正文 <20000 字符且 <1MB；封面走素材接口单独上传
- 封面必填：缺失报 40007，宽不足报 53401
- 2025-07 起未认证主体无发布接口权限（48001），只能草稿箱 + 后台手动群发

# 维护约定

- **详细改动写 briefing，不写这里**：本文是索引，briefing 是唯一正文，两者不得重复描述同一件事。
- **视觉变动改两个地方**：`assets/themes/*.json`（数值）+ `references/visual-design-brief.md`（说明），两者必须同步。
- **改了 scripts/ 必须跑** `python scripts/selftest.py`（36 项断言）。
