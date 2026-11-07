# 输入形态识别与归一

> 本文管「把用户给的任何形态归一成 markdown」。归一之后才能进章节底账 / 分块 / 翻译。
> 论文按节取舍见 paper-disposition.md；分块与成本见 chunking-and-cost.md。

## 1. 输入有两种，先归成统一的 markdown，再翻译

**标题结构从哪来（按来源分层，越往下越要人确认）**：

| 来源 | 结构信息 | 获取方式 |
|---|---|---|
| HTML | 有语义标签 `<h1>–<h6>` | `preprocess.py`，零猜测 |
| Word/docx | 有样式名（标题 1 / Heading 2） | 解析样式逐段映射（未遇到真实样本，不预建） |
| PDF 文字层 | 有字号/加粗信息 | 按字号层级聚类（同上） |
| OCR / 手复制 | **什么都没有** | `sections.py detect`：输出候选 + 规则 + 置信度，**mid 只列出，不硬猜** |

关键认知：前两种来源根本不需要「猜目录」，结构在源格式里就有；
OCR 的正确态度不是猜得更聪明，而是**候选 + 证据 + 置信度交给人**。

## 2. A. 纯文本（可能从网页手复制，格式已丢失）

按顺序识别并补全，**置信度低的保持原样，不要硬猜**：

| 特征 | 判为 | 置信度 | 补全？ |
|---|---|---|---|
| 含 `def` / `import` / `class` / `{` / `;` 或整段等宽缩进 | 代码块 | 高 | 补 ``` 围栏 |
| 行首 `-` `•` `*` `1.` `1)` | 列表 | 高 | 补 |
| 独立成行、无句号、< 40 字、前后空行 | 标题 | 中 | **不补**，列出疑似项交给人 |
| 多列空格/制表符对齐 | 表格 | 中 | **不补**，同上 |

过度补全 = 幻觉格式，比不补全更糟。疑似项在输出末尾单独列一节「未确定的格式」。

## 3. B. HTML

1. 抽取正文（去掉 `script` / `style` / 导航 / 页脚）
2. 保留 `h1–h6` / `p` / `ul` / `ol` / `pre` / `table` / `blockquote` / `img`
3. 转成 markdown

**图片必须处理这三件事**：

| 情况 | 处理 |
|---|---|
| 相对路径（`/images/foo.png`） | 用源站 base_url 补成绝对 URL，否则 md 打开渲染不出 |
| lazy-load（`src` 是占位符，真地址在 `data-src` / `data-original`） | 取 `data-src` |
| `<figure>` 带 `<figcaption>` | 图注也要翻，放在图片下方 |

输出一律用 `![alt](url)`，alt 缺失时按上下文补一个简短中文 alt。

```bash
python skills/translate-zh/scripts/preprocess.py <html文件> [输出md] [base_url]
python skills/translate-zh/scripts/preprocess.py <目录> [输出目录] [base_url]
```

## 4. B-3. URL 输入：先 fetch 再走上面流程

用户消息里带 URL（口语化、多个混排都行）时，用 `fetch.py` 先落到约定目录 ——
全链确定性：URL 提取是正则、分类是映射表、查重是文件存在性、下载是 import
既有爬虫的函数。**不需要「下载 agent」**。

```bash
echo "翻译 https://arxiv.org/abs/2609.24972 和这篇 https://aclanthology.org/2024.acl-long.1/" > msg.txt
python skills/translate-zh/scripts/fetch.py plan msg.txt      # 提取/分类/查重 -> outputs/fetch-plan.json
python skills/translate-zh/scripts/fetch.py fetch             # 只下载"待下载"项，已存在的不重下
python skills/translate-zh/scripts/fetch.py test              # 13 个离线用例
```

| 来源 | 落盘位置（根目录由 `.env` 配置，见 `_config.py`） | 复用的既有代码 |
|---|---|---|
| lilianweng / langchain / anthropic 博客 | `CRAWLERS_ROOT/<站点>/output/articles/<slug>/`；爬虫仓库不在时降级为 `outputs/articles/<站点>/` | 各爬虫的 `fetch_article()`，缺失时通用抓取存 HTML |
| ACL（含 EMNLP 等，`<年>.<venue>-<track>.n`） | `ACL_ROOT/<VENUE>/<年>/<ID>/raw/` | acl-anthology-downloader 的 `download_paper()`，缺失时直接下 PDF |
| arXiv | `ARXIV_ROOT/<年>/<ID>/raw/`（结构与 ACL 对齐） | fetch.py 直接下载 |

博客抓完 → 走 §3（HTML 路线）。论文抓完拿到 `raw/paper.pdf`，**按 PDF 类型走两条路**：

**路径 A：born-digital（arXiv/ACL 新论文绝大多数是这种，先试这条）**

```bash
python skills/translate-zh/scripts/pdf2md.py <raw目录>/paper.pdf     # pypdf 抽文字层 -> paper.md
python skills/translate-zh/scripts/sections.py detect paper.md --apply -o paper.structured.md
```

全程本地确定性代码，不调服务。pdf2md 判定是扫描件会退出码 2，此时走路径 B。
pypdf 抽取会有空格粘连伪影（如 `andcontextmanagement`），标题结构由 sections.py detect
补（HIGH 插为标题，MID 列出待人确认）。LaTeX 源 + pandoc 能给出最完整的结构，仍是更优
来源，但不再是必需（pandoc 本机未装，缺了不影响闭环）。

**路径 B：扫描件（pdf2md 退出码 2 才走）**

```bash
python skills/translate-zh/scripts/ocr_pdfs.py --input <raw目录>      # PaddleOCR-VL，需 OCR_TOKEN（见 .env.example）
python skills/translate-zh/scripts/paddle2md.py <上一级>/<paddle_ocr_vl_1_6>   # json -> markdown
```

OCR 是重服务调用，不自动跑。服务端每页返回的 page_content 本身就是 markdown（带 # 标题），
paddle2md 只做确定性拼装：page_content 依次拼接 + fig 块转 `![]()` + 图片相对路径改写
（图统一放页尾，因为 json 不保留图在页内的位置）。

已知缺口（记录在案，遇到再修）：

- 博客抓完的 md **没有 H1 主标题**（爬虫把标题存在 article.json 元数据里，
  preprocess.py 未回填）—— 目前人工按「中文译题（English Original Title）」补双语标题；
- born-digital PDF 的公式是 OCR 不了的纯文本碎片，`$$` 守恒校验在 PDF 来源上不适用
  （守恒对比只对 HTML/博客来源可靠）；
- 旧式 ACL ID（无点分，如 A00-1001）不支持，fetch.py 会如实报告。
