# 论文 / 长文档：先按节取舍，再翻译

> 本文管「翻译前哪些内容不翻 / 可配置取舍」。全部用 `sections.py` 纯正则处理，**不用 AI**：
> 没有参考文献的论文不命中就原样输出（天然幂等），AI 反而引入判断漂移。
> 输入归一见 input-preprocessing.md；分块与成本见 chunking-and-cost.md。

## 1. 用法

```bash
python skills/translate-zh/scripts/sections.py list paper.md          # 先看：每个标题标 保留/跳过 + 命中规则 + 各节 token
python skills/translate-zh/scripts/sections.py strip paper.md -o clean.md --skip refs,citation   # 再剔
python skills/translate-zh/scripts/sections.py test                   # 内置 12 个边界用例
```

`list` 的末行给出总账：`全文 X tok / 可剔 Y tok / 剩 Z tok` —— 这是成本闸门（--factor）的输入。

## 2. 输入是 OCR / 手复制的无结构文本时：先 detect 找标题

```bash
python skills/translate-zh/scripts/sections.py detect ocr.txt                # 列出候选：HIGH / MID / TOC + 命中规则
python skills/translate-zh/scripts/sections.py detect ocr.txt --apply -o paper.md   # HIGH 插为标题，MID 不动待确认
```

识别规则：多级编号（3.2）、罗马数字分节（II.）、全大写短行、常见章节词
（Introduction / References / 方法…）→ HIGH；孤立短行无标点 → MID（人确认）；
**列表连排（相邻的 1. 2. 3.）被抑制** —— 那是列表不是标题；点引导线
（Introduction ..... 5）→ 目录页标记。

## 3. 匹配规则：归一化后整词，绝不用子串

语料实测有 "API references"、"Respecting model preferences" 这类标题，
子串匹配会误删正文。归一化顺带吃掉变种：

- "R eferences"（PDF 空格粘连）
- "6. References"（编号）
- "REFERENCES"（全大写）

全部命中 "references"。**剔除范围 = 标题行 → 下一个不深于它的标题（不是文件尾！）**——
"References 后面跟 Appendix" 时附录不会陪葬。

## 4. 内容取舍表（论文 / 长文档全类型）

| 内容 | 处理 | 谁决定 |
|---|---|---|
| 文章主标题 | 双语：中文译题（English Original Title）—— 检索需要 | 规则 |
| 章节标题 | **仅中文，保留编号**（3.2 ↔ 原文 3.2 对照靠编号，不靠英文）| 规则 |
| 目录 TOC | **删** —— 译文标题本身就是目录，留原目录是噪音 | `--skip toc` |
| References / Citation / bibtex | **整节剔除** | 默认剔除 |
| 致谢 / 作者贡献 / 基金 / 利益冲突 / 数据可用性 | 默认保留，要剔加 `--skip ack,contrib,funding,conflict,data` | 可配置 |
| 附录 Appendix | 默认**保留**（可能有正文引用的证明，删了丢信息），要剔加 `--skip appendix` | 可配置 |
| 摘要 / 正文 / 图注 | 译 | — |
| 作者 / 机构 / 邮箱 | 不译，原样保留 | 规则 |
| 关键词 | 译 + 英文对照（关键词检索需要原文）| 规则 |
| 页眉页脚（页码 / 期刊名 / running head）| **删** —— 无信息量，是 Word/PDF 转换残留 | 翻译时删 |
| 脚注 | **译** —— 常有关键补充（URL、细节），保留编号，放所在段落后 | 规则 |
| 交叉引用 "see Section 3.2 / Figure 4" | 译字面保编号：第 3.2 节 / 图 4 / 表 2 | 规则 |
| 引用标记 [1] [12,15] | 原样保留 | 规则 |
| 表格 | 表头译，数据单元格不译 | 规则 |
| 公式 / 代码 / 参数量 | 不译 / 不动（硬边界见 execution-rules.md）| 规则 |
| 缩写表 Nomenclature | 保留英文，逐条按 glossary 处理 | 规则 |

## 5. 已知缺口

- **页内锚点失效**：标题译成仅中文后，`#relative-position-encoding` 这类页内锚点
  全部失效（实测遇到）。需要主 agent 维护「原英文标题 ↔ 中文标题」全局映射表
  做链接改写 —— 待办，遇到第二篇论文前建好。
