# translate-zh · EN→ZH 翻译流水线技能包

## 1. 目的

把「翻译一篇英文内容」拆成两类工作，用**自包含任务包**衔接：能枚举的确定性规则交给脚本
（token 计量、成本核算、按结构分块、章节剔除、URL 抓取、PDF→markdown），判断工作交给
只读任务包的全新 agent。核心是两件事：**不让翻译者看到全文**（用锚点+词典保证拼接连贯），
**不让钱失控**（开翻前必跑成本估算，超过确认线要用户点头）。

## 2. 什么时候用

- 用：用户给英文文本 / HTML / 消息里的 URL / PDF 论文（arXiv、ACL）要高质量中文译文；
  在意术语一致性、结构守恒、长文不漏译。
- 不用：只想总结成几句话；非 EN→ZH；工作根下没有 `data/`（bundle 会直接报错，不静默降级）。

## 3. 部署（新机器 / 拿到本包的人）

1. Python 3.10+，装依赖：`pip install requests beautifulsoup4 pypdf`
   （`fetch.py` / `pdf2md.py` / `ocr_pdfs.py` 用；`chunk.py`、`sections.py`、`preprocess.py`
   `paddle2md.py` 等只用标准库）。
2. 把 `.env.example` 复制为 `.env`（**就近放在包根**），按本机情况改路径与凭据。
   全部可省略：省略时论文落包内 `corpus/`，外部爬虫缺失自动降级，born-digital PDF 全程可用。
3. 命令在**工作根**下执行。两种目录布局都支持，`scripts/_config.py` 按父目录名判别：

   | 布局 | 包的位置 | 工作根 |
   |---|---|---|
   | A 原项目 | `<root>/skills/translate-zh/` | `<root>`（项目根） |
   | B 本仓库 | `<repo>/translate-zh/` | 包自己（`<repo>/translate-zh/`） |

   两种布局下 `data/` 都必须存在（本包自带，无需从别处拷）。

## 4. 输入到 markdown 的完整链路

| 输入 | 处理 | 脚本 |
|---|---|---|
| 纯文本 / 手复制 | 低置信度格式不硬猜，疑似项列出来交人 | 人工 + `sections.py detect` |
| HTML / 博客 | 语义标签转 markdown，图片补绝对 URL 与图注 | `preprocess.py` |
| URL | 正则提取 → 分类 → 文件存在性查重 → 下载 | `fetch.py plan` / `fetch.py fetch` |
| born-digital PDF | pypdf 抽文字层（平均每页 <200 字符判扫描件，退出码 2） | `pdf2md.py` |
| 扫描件 PDF | PaddleOCR-VL 服务 → paddle json → markdown | `ocr_pdfs.py` → `paddle2md.py` |

**PDF 两条路（先试 A，失败才走 B）**：

```bash
# A：born-digital，本地确定性，不调服务
python translate-zh/scripts/pdf2md.py <raw目录>/paper.pdf
python translate-zh/scripts/sections.py detect paper.md --apply -o paper.structured.md

# B：扫描件，OCR 是重服务调用，需自备 token
python translate-zh/scripts/ocr_pdfs.py --input <raw目录>
python translate-zh/scripts/paddle2md.py <上一级>/<paddle_ocr_vl_1_6>
```

`paddle2md.py` 是纯确定性拼装：服务端每页的 `page_content` 本身就是 markdown，
只需拼接 + 把 fig 块转成图片链接 + 按输出位置改写相对路径。图统一放页尾——
json 不保留图在页内的位置，这是有损点，如实声明。

## 5. 翻译主流程（脚本速查）

```bash
python translate-zh/scripts/chunk.py cost  <md>                    # 开翻前必跑，展示估算
python translate-zh/scripts/chunk.py count <md>                    # ≤5000 整翻，>5000 分块
python translate-zh/scripts/sections.py list <md>                  # 章节底账（纯正则，零 token）
python translate-zh/scripts/chunk.py split <md> --out-dir <dir>    # 按结构切，不切碎代码块/表格
python translate-zh/scripts/chunk.py bundle <dir> --index N        # 自包含任务包（隔离靠数据构造）
python translate-zh/scripts/chunk.py join <dir> -o final.zh.md
python translate-zh/scripts/chunk.py reconcile final.zh.md         # 统一重复清单项译法
python translate-zh/scripts/chunk.py conservation <源.md> <译.md>  # 交付前守恒校验
```

分层规则见 `references/`（按需读，不要全量加载）：输入归一、论文按节取舍、分块与成本、
翻译判断准则、评估与修订、设计原则。入口 `SKILL.md` 有完整执行顺序。

## 6. .env 配置项

优先级：**系统环境变量 > 包内 `.env` > 默认值**。`.env` 不入库，`.env.example` 是模板。

| 键 | 作用 |
|---|---|
| `ACL_ROOT` / `ARXIV_ROOT` | 论文落盘根，默认 `<工作根>/corpus/...` |
| `CRAWLERS_ROOT` | 博客爬虫仓库根，缺失时降级为通用抓取存 HTML |
| `ACL_DOWNLOADER` | ACL 下载器仓库根，缺失时直接下 PDF |
| `OCR_TOOLKIT` | 可选，仅用于打印扫描件 OCR 提示 |
| `OCR_TOKEN` | official 模式 OCR 必填（PaddleOCR aistudio 免费注册获取） |
| `OCR_MODE` / `OCR_LOCAL_API_URL` / `OCR_LOCAL_API_TOKEN` | official（云 API）或 local（自建服务） |
| `OCR_ORDER` / `OCR_OUT_DIR_NAME` / `OCR_JSON_NAME` | 解析顺序与落盘命名 |

## 7. data/ 是什么

| 文件 | 角色 |
|---|---|
| `glossary.yaml` | 术语唯一真相源，`bundle` 只投影本块命中的词条 |
| `examples.md` | 好坏译文对照，随任务包下发 |
| `candidates.md` | `scan_corpus.py` 扫出的未确认候选 |

修改术语请改 `data/`，不要改合并后的成品；译文回填要落在块文件再 `join`。

## 8. 已知边界

- pypdf 抽文字层有空格粘连伪影（如 `andcontextmanagement`），标题结构靠 `sections.py detect`
  补（HIGH 自动插，MID 列出待人确认）；LaTeX 源 + pandoc 是更优结构来源，但不是必需。
- PDF 来源的公式是纯文本碎片，`$$` 守恒校验只对 HTML / 博客来源可靠。
- 旧式 ACL ID（无点分，如 `A00-1001`）不支持，`fetch.py` 会如实报告。
- 爬虫 / ACL 下载器 / OCR 服务都是**可选增强**：缺了前者自动降级，缺了 OCR 凭据只是扫描件
  不能翻，born-digital 全流程不受影响。

## 9. 安全说明

本包不含任何真实凭据：`.env`（含 token）、`outputs/`、`corpus/`、`__pycache__/` 一律不入库，
见 `.gitignore`。所有本机绝对路径只出现在 `.env.example` 的注释占位符里，代码内路径全部
由 `scripts/_config.py` 从脚本位置相对推导。
