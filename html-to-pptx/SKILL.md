---
name: html-to-pptx
description: 把「用 HTML/CSS 做成的幻灯片」转成真正可编辑的 .pptx（文字、表格、图片都能单独改，讲稿进 PowerPoint 备注页），而不是把每页截图贴进 PPT。当用户说"我把 PPT 用 HTML 做的，想转成 .ppt/.pptx""这个 html 幻灯片能变成 PPT 吗""网页版汇报想导出成 PowerPoint"之类时使用，也适用于转换后对某一页改稿（改目录树、改文字、换图）。核心依赖 workbuddy-pptx 技能自带的 slidep 工具链；本包只负责 HTML→PPT 这套决策、素材归置、讲稿改写、单页替换的流程与踩坑，不重复造 PPT 生成引擎。关键词：HTML 幻灯片转 PPT、html 转 pptx、网页转幻灯片、可编辑 PPT、讲稿进备注、逐页截图（反例）。
agent_created: true
---

# HTML 幻灯片 → 可编辑 PPTX

## 1. 目的先行

HTML 幻灯片（`<section class="slide">` + CSS 排版 + 可选 JS 翻页）**没有一键高保真转换成可编辑 PPT** 的工具。本技能走的是「按 HTML 内容，用 PPT 原生元素重新排一版」的路线：

- 文字、表格、图片是真正可编辑的对象，不是整页截图；
- 每页 HTML 里的讲稿（`.notes`）改写后写入 PowerPoint **演讲者备注**；
- 配色、卡片风格沿用原 HTML，版式重排但不逐像素照搬。

**前提**：本机的 PPT 生成能力来自 `workbuddy-pptx` 技能的 `slidep` CLI（create / upsert-dsl / lint）。动手前先确认该技能可用；本包不实现生成引擎，只提供这套转换的场景知识。

## 2. 何时使用 / 何时不用

| 情况 | 用不用 |
|---|---|
| 有一份 HTML 幻灯片，要可编辑的 .pptx | ✅ 用 |
| 转换后要对某一页改文字/改目录树/换图 | ✅ 用（见 §5 单页改稿） |
| 用户明确要"画面和浏览器里一模一样、能放映就行" | ⚠️ 先确认：这条走逐页截图，不可编辑，见 §7 反例 |
| 纯 `.pptx` 之间编辑、或全新主题生成 PPT | ❌ 走 `workbuddy-pptx` |
| 只是把 HTML 打印成 PDF | ❌ 与本技能无关 |

## 3. 决策树（第一步必须先对齐）

```
用户给了 HTML 幻灯片
├─ 没说明要哪种 → 问：可编辑重建（推荐）还是逐页截图保真？
│    └─ 讲稿怎么办？→ 默认写进备注页（推荐），或丢弃
├─ 选「可编辑重建」→ 走 §4 流程
└─ 选「逐页截图」→ 告知不可编辑、讲稿无处放，用户坚持再执行
```

**关键：意图未对齐前不要动手。** 可编辑版版式不会和 HTML 逐像素一致，这一点要在开始前讲清楚，否则用户会拿浏览器截图逐页比对。

格式说明：生成的是 `.pptx`（PowerPoint 2007+ 通用格式）。用户说"`.ppt`"通常是泛指；若确实要老 `.ppt` 后缀，需在 PowerPoint 里「另存为 → PowerPoint 97-2003」，命令行生成不了。

## 4. 完整转换流程

1. **解析 HTML**：跑 `scripts/inspect_html.py <html> -o deck.json --pretty`，拿到页数、每页标题、讲稿、图片引用、表格/目录树。人工快速核对解析结果。
2. **确认交付选项**（见 §3 决策树）：可编辑 or 截图，讲稿进备注 or 丢弃。
3. **归置素材**：把 HTML 引用的图片复制到 `<项目>/assets/`，改成**英文文件名**（禁用中文/空格/特殊字符），跑 `scripts/check_images.py assets/` 看宽高比和是否有过小图。
4. **建项目结构**：`<项目名>/`（目录名 = pptx 文件名去后缀）、`assets/`、`slides/`、`项目名.pptx`。用 `slidep create` 建空文件，立即 `present_files` 打开预览。
5. **逐页生成**：每页写 `slides/NN.slide`，然后 `slidep upsert-dsl` 追加、`slidep lint` 检查；**先写入再检查**，每页 `ok:true` 才进下一页。下标从 0 递增自己维护，不按文件名猜。
6. **讲稿**：写进 `<Slide notes={...}>`。改写规范见 [speaker-notes.md](references/speaker-notes.md)，字数/措辞用 `scripts/check_notes.py slides/ --require` 校验。
7. **交付**：`present_files` 打开成品。

DSL 组件写法、布局硬约束、图片/表格语法速查见 [dsl-quickref.md](references/dsl-quickref.md)。
完整踩坑清单与正反例见 [pitfalls-and-examples.md](references/pitfalls-and-examples.md)——**动手前必读**。

## 5. 单页改稿（转换后最常用）

用户看完要改某一页（如目录树加一层分类）时，**不要重新生成整份**：

1. 直接改对应的 `slides/NN.slide` 源文件；
2. `slidep upsert-dsl <pptx> --dsl-file slides/NN.slide --page-index <NN-1>` 替换该页（`--page-index` 是 **0-based**，第 9 页 = 8）；
3. `slidep lint <pptx> --dsl-file slides/NN.slide --page-index <NN-1>` 检查；
4. `present_files` 刷新预览。

同时改 HTML 原文件时，**两处内容要一致**（正文、目录树、讲稿三处最容易漏）。真实案例见 [pitfalls-and-examples.md](references/pitfalls-and-examples.md) 的「目录树改稿」。

## 6. 资源清单

- `scripts/inspect_html.py`：HTML → 结构化 JSON（页/标题/讲稿/图片/表格/树）。
- `scripts/check_images.py`：图片尺寸、比例、大小，提示过小图。
- `scripts/check_notes.py`：讲稿字数（≤300）、禁用元描述、残留前缀校验。
- `references/speaker-notes.md`：讲稿改写规则与正反例。
- `references/dsl-quickref.md`：Slide/Box/Text/Image/Table 速查 + 布局约束。
- `references/pitfalls-and-examples.md`：踩坑清单 + 完整正反例（必读）。

## 7. 一句话记住反例

**不要**把 HTML 每页截图贴进 PPT 当"转换"——那是图片，字改不了、讲稿无处放；**不要**把讲稿原文连"讲稿："前缀整段塞进备注（超 300 字会溢出、有元描述不合规范）；**不要**用 `display:grid` / `calc()` 写 DSL（不支持，用 flex）。
