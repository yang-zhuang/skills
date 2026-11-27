# scripts/ 使用说明

skills/content-abridge 的全部机制程序。**只做机制**（转换、构造、校验、聚合），不含任何判断——判断规则在 `references/`。所有脚本接受位置参数，无 flag。

## 环境（fresh-env 可跑）

解释器用 managed venv（绝对路径，勿用系统 python）:

```
C:\Users\lenovo\.workbuddy\binaries\python\envs\default\Scripts\python.exe
```

依赖（2026-10-02 已装好，重装机器时执行）:

```
...python.exe -m pip install -i https://pypi.org/simple beautifulsoup4 markdownify requests pyyaml lxml
```

> 坑：本机 pip 全局配置的清华镜像 URL 带 `/pypi/web/simple` 后缀（错误路径），所有包都报
> "versions: none"。清华镜像在这台机器上也不通。**必须加 `-i https://pypi.org/simple`**。

## 流水线顺序

```
convert.py -> build_task_pack.py -> classify.py -> [起草]
-> verify_assets.py + verify_citations.py + verify_blocks.py
-> [评审] -> validate_review.py -> [修订]
-> verify_assets.py + verify_citations.py + verify_blocks.py -> ...循环 N=3（无 must-fix 提前终止）
-> aggregate.py（跨任务，演化方针用）
```

**长文档分支**（source.md >= 30,000 字符，classify 之前插入；DESIGN.md §10.3-B）:

```
convert.py -> build_task_pack.py -> classify.py
-> build_outline.py（全文目录）-> [trimmer 一轮裁决 trim-decision.yaml]
-> execute_trim.py（校验 -> 整节删除 -> 达标即停）
   达标 -> [起草]（与短文档同一流程）
   仍超 -> build_outline.py（对剥离版重跑）-> [trimmer 二轮裁决 simplify-directives.json]
        -> [起草]（带顶级骨架约束与简化指令，同一流程）
```

方括号 = skill 的角色（LLM 子 agent 执行），其余为本目录脚本。

历史任务包（convert 时还没有自动还原转义的）先跑一次 `repair_math.py` 再进入流水线。

## 各脚本

### convert.py —— 本地 HTML 转 markdown + 图片本地化

```
python convert.py <article_dir> <out_dir>
```

- `<article_dir>`：语料文章目录（含 content.html，可选 meta.json 供 base url）
- 产出：`source.md`（图片引用 `assets/img-NNN.ext` 相对路径）、`assets/`、`assets-manifest.json`
- 下载失败的图保留原 URL，manifest 标 failed；data: 内联图跳过
- 转换后**自动调用 `repair_math.py` 的还原函数**：修掉 markdownify 在数学区/代码区转义出的 `\_`（不修会让下标塌陷，见附录 A）。若手动加工 md，可单独跑 repair_math.py。

### convert_paddle.py —— PaddleOCR-VL JSON 转 markdown（ACL 语料）

```
python convert_paddle.py <article_dir> <out_dir>
```

- `<article_dir>`：语料文章目录（含 `paddle_ocr_vl_1_6/paddle_ocr_vl.json` 与 `imgs/`）
- 产出与 convert.py 同构（source.md / assets/ / assets-manifest.json），`build_task_pack.py` 输入契约不变
- **front matter 拦截**（实测 2026-10-05）：metadata title 以 "Proceedings of" 开头即拒绝（各卷 .0 是论文集 front matter，不是论文），exit 1 无产出
- **已知限制（上游缺陷，不可修，2026-10-06 核实）**：PaddleOCR-VL 会把表格里的图标误读成货币字符——`2026.acl-long.270` Table 4 表头 `Evidence (£)` / `Targets (₪)` 在**上游 JSON 里就已是 `£` / `₪`**（原文是图标）。转换层忠实转录、表格逐字节铁律又禁止稿件层修复。不再就此出 policy-gap；如需修复须重跑 OCR 或人工改表后重建任务包。

### repair_math.py —— 数学区/代码区的转义污染修复

```
python repair_math.py <file.md> [<file2.md> ...]
```

- 原地改写、幂等（第二次起替换数恒为 0）
- 只动四类"保护区域"里的 `\_` / `\*`：围栏代码块、行内反引号、`$...$`、`$$...$$`（可跨行）。区域外的 `\_` 是正文里防斜体的正当转义，一个都不动
- LaTeX 真命令（`\mathbb`、`\{`、`\}`、`\\`）不在解转义集合里，绝不误伤
- 顺带列出"数学/代码区之外的疑似裸上标"（如作者自己写的 `RL^2`）供角色判断，**不自动改写**——要不要包 `$...$` 是判断（方针 B6），不是机制
- 实测（`20261003-tfam` 原文 / `20261003-agents` 原文与初稿）：差异全部是单个反斜杠删除，共 277+26 处，代码块逐字节比对仍全部命中

### build_task_pack.py —— 构造任务包

```
python build_task_pack.py <converted_dir> <runs_dir> <skill_dir> <task_id>
```

- 从 converted_dir 建 `runs/<task_id>/`，复制方针快照进 `policy/`，meta.json 初始 `type=uncertain`
- skill_dir 传本 skill 包目录（content-abridge/）
- **只组装不剥离**（2026-10-05 重做）：全文原样入包，长文档的删除一律走 trimmer 裁决 + execute_trim 执行；v1 的"打包时正则直剥 References/附录"已废弃（它折叠空行、绕过四道门）
- **顺带生成 `input/refs-candidates.json`**（调 extract_refs.py）：原文全部外链的候选引用表，供起草角色按方针铁律 10 挑用，避免自造 URL
- 依 `thresholds.THRESHOLD_CHARS` 判 mode：`>=` 阈值写 `mode=long` + `state.stage=trim-delete-pending`，否则 `mode=short` + `stage=ready`

### thresholds.py —— 长文档判定阈值的唯一事实来源

无 CLI，只导出 `THRESHOLD_CHARS`（当前 30,000）。

- 被 build_task_pack.py（决定 mode）与 execute_trim.py（决定达标即停）共同 import，**同口径**
- 独立成文件的原因：此前两处各自硬编码 `30_000`，改一处漏一处会静默产生"pack 判 long 先走 trim、execute_trim 却认为已达标直接放行"的不一致；不能简单让 build_task_pack 去 import execute_trim，后者模块级 `import yaml` 会把 pyyaml 变成打包环节的硬依赖（打包目前裸解释器即可跑）

### classify.py —— 类型判定（唯一依赖 LLM endpoint 的脚本）

```
python classify.py <task_pack_dir>
```

- 环境变量：`ABRIDGE_LLM_BASE_URL`（默认 http://127.0.0.1:8000/v1）、`ABRIDGE_LLM_API_KEY`（默认 EMPTY）、`ABRIDGE_LLM_MODEL`（默认 qwen，按 endpoint 实际模型名设置）
- 结果写回 meta.json 的 type；置信不足写 uncertain（角色文件规定：uncertain 即终止报告）

### extract_refs.py —— 原文外链抽取（候选引用表）

```
python extract_refs.py <source.md> <out.json>
```

- 逐条抽出原文的 http(s) 链接：URL、锚文本、出现次数、行号、上下文、是否落在 `![]()` 里
- 正常由 build_task_pack.py 调用，落在任务包 `input/refs-candidates.json`；单独跑可用于补做历史任务包

### build_outline.py —— 长文档目录提取（trimmer 一轮/二轮的输入）

```
python build_outline.py <source.md> <out.json>
```

- 解析全部 `#` 标题（跳过围栏代码块内部的 `#`），每节产出 `{id, path, level, title, chars, figs, first, last, flags}`
- **id = `L<起始行号>` 是唯一键**——同名标题实测会撞名（`Output format` × 146），机器引用一律用 id；`path` 面包屑只给人看
- `chars` 是节子树去空白行字符量（删除该节实际消失的量，trimmer 预算视角）；`figs` 是子树内 `![](assets/...)` 行数（铁律 1：含图节是高危删除对象）
- `first`/`last` 首末句预览各截 120 字符（句切分屏蔽 `et al.`/`Fig.`/小数/`$...$`/行内代码，防 preview 变噪音）
- `flags` 机械打标：`parent` / `no-body` / `in-appendix` / `numbered` / `suspect-split`，只呈现不静默处理
- 幂等（同一输入字节一致输出，sha256 已验）；对全文和剥离版都能跑，长文档流程里跑两次
- 实测：acl-long-270 副本（106k 字符）43 节，id 唯一、附录 A/B/C 全部标中、首末句抽查与原文逐句一致

### execute_trim.py —— 校验裁决并执行整节删除（四道门第三道）

```
python execute_trim.py <task_pack_dir>
```

- 前置：`input/source.md`（全文）+ `input/outline.json` + `input/trim-decision.yaml`（trimmer 一轮裁决）
- **校验任一不合格 = 整份打回**（列出全部错误、exit 1、零改动）：id 存在且不重复、区间不重叠（父子同列属意图不明）、priority >=1 整数、reason 非空、Limitations/Ethical Considerations 硬约束禁删、outline 与 source.md 逐节比对（目录陈旧即打回）、YAML 单文档无 `---`
- 执行：figs>0 一律 `held` 不执行（图归属有误判风险，宁漏勿错，编排器复查）；priority 1（方针判删必删）恒执行；priority 2+ 删到剩余 <= 30,000 字符即停，未到的记 `skipped-budget`
- `--validate-directives`：二轮模式，只校验 `input/simplify-directives.json` 不执行（id 存在/不重复/区间不重叠/ratio 0-1/instruction 非空/硬约束禁指；"是否简化保留档"属判断，归 trimmer 自查 + 评审核对）
- 剥离 = **纯整行删除**，不改写不折叠空行；产出 `source-full.md`（全文留档）+ 剥离版 `source.md` + `trim-manifest.json`（每段 status/chars/reason + 统计）
- 自检（脚本内置 assert）：留档与执行前字节一致、删除行集合 == manifest 宣称区间、落盘剥离版 == 计算结果
- 阈值口径：30,000 用 `len(全文文本)`；outline 的 chars 是去空白行口径，两者不同、各有用途
- 实测：13 条裁决 -> deleted 11 / held 1（figs=4）/ skipped-budget 1，106k -> 28,017 字符达标；打回路径 5 类错误全数拦截且文件零改动（sha256 验证）

### verify_citations.py —— 引用合规校验（方针铁律 10 的机器检查）

```
python verify_citations.py <task_pack_dir> <md_file>
```

三条硬检查，任一不过即非零退出：
1. **URL 出处**：稿件里每个 http(s) 链接都必须能逐字节在 `input/source.md` 里找到；找不到 = 自造/替换/补写
2. **编号对应**：正文每个 `[n]` 必须在文末 `## 参考资料` 有条目；反之每个条目也必须被正文引用过（不许列无人引用的参考书目）
3. **编号连续**：1..N 无缺号、无重号、无同一 URL 占两个编号；`[n]` 按编号升序首次出现

解析依据的稿件格式（`references/draft.md` 输出契约里同样写明）：

```markdown
## 参考资料

- [1] Shen et al. 2023: HuggingGPT <https://arxiv.org/abs/2303.17580>
```

与 `verify_assets.py`、`verify_blocks.py` 并列：起草/修订产出后三个都要跑。

### verify_blocks.py —— 围栏块三档溯源（铁律 2 的机器检查）

```
python verify_blocks.py <task_pack_dir> <md_file>
```

- 稿件里每个围栏块（代码 / 命令 / 提示词 / JSON）的内容都必须能在 `input/source.md` 里找到，三档判定：
  - `逐字节`：原文中存在完全相同的字符串；
  - `去转义`：两边剥掉 markdown 转义（`\_` `\*` `\[` `\]`）后相同——同一段文字在原文落在段落里
    （markdownify 转义 `_`）、在稿件里落进围栏（`repair_math.py` 已还原）时会出现这档，属正常。
  - `行级`：整块找不到，但块内**每一行**都能在原文单独找到——方针 B7 强制伪代码/提示词进围栏，
    而 PaddleOCR 转出的原文伪代码是「空行分隔的散行」，收进围栏必然改变换行结构。放行换行规范化、
    守住逐行内容不改，避免 B7 与铁律 2 直接打架（2026-10-05 长文档首次起草实测暴露）。行级档逐块列出，可见不静默。
- 三档都找不到 = 内容被改写，违反铁律 2，非零退出（回归测试：`K = 5`→`K = 9` 仍被拦截）
- 顺带打印用到的围栏**语言标记清单**（方针 B7：提示词不写 ```python、JSON 写 ```json、拿不准就不加标记），便于评审与人工校对
- 在此之前这一步是编排器临时写的一次性命令，2026-10-04 固化为本脚本

### validate_review.py —— 评审 YAML schema 校验

```
python validate_review.py <review.yaml>
```

- 校验字段/枚举/id 唯一性；不合格非零退出并逐条列出问题（打回重出的依据）

### verify_assets.py —— 图片引用一致性校验

```
python verify_assets.py <task_pack_dir> <md_file>
```

- 校验 md 内图片引用全部可解析；draft/revised 同级 assets/ 不允许有未引用的图
- 长文档（input/source-full.md 存在时）：引用了"被 trim 删掉的段的图"→ FAIL（判定 = 全文图集 − 剥离版图集，与 trim-manifest 行范围严格等价；场景：末页图被 OCR 归进 References 节、trim 后消失，draft 再引即漏查）
- 起草/修订角色产出后必须跑一次，通过才算该轮完成

### aggregate.py —— defer 意见跨任务频次聚合

```
python aggregate.py <runs_dir>
```

- 按 (dimension, problem 前 50 字) 分组计数，写 `runs/_defer-aggregate.json`
- count >= 3 的组合交人工 review，决定是否升为方针规则（DESIGN.md §5.2）

---

## 附录 A：`\_` 为什么必须修（KaTeX 实测，2026-10-04）

用户反馈跑完第六个任务包后"公式上下标有问题"。定位过程与证据如下。

**现象**：任务包 `20261003-agents` / `20261003-tfam` 的原文里全是 `$a\_t$` 这种写法。

**成因**：`convert.py` 用 markdownify 转 HTML，markdownify 为防止正文里的 `_` 被当成斜体，统一转义成 `\_`；它不认识 `$...$` 是 LaTeX。于是作者写的 `$a_t$` 落盘时就变成了 `$a\_t$`。同一问题也会污染行内文本里的标识符（如 prompt 模板里的 `task\_id`，它在原文里本就没落进代码块）。

**实测**（managed node + katex 渲染取 HTML 骨架）：

| 源 | MathML 骨架 | 结果 |
|---|---|---|
| `a_t` | `<msub><mi>a</mi><mi>t</mi></msub>` | 正常下标 |
| `a\_t` | `<mi>a</mi><mi mathvariant="normal">_</mi><mi>t</mi>` | **下划线变成一个字面字符，下标消失** |
| `W^k_i` | 含 `<msub>`/`<msup>` | 正常 |
| `W^k\_i` | 无 `<msub>` | 下标消失 |
| `r_n \geq r_{n-1}` | 含 `<msub>` | 正常 |
| `r\_n \geq r\_{n-1}` | 无 `<msub>` | 下标消失 |

即从 12 个抽样公式里，转义过的那些**没有一个产出 `<msub>`**；修复后 12/12 全部产出正确的 `<msub>`/`<msup>`。MathJax 行为相同。

**修复落料**：`repair_math.py`（可回填历史任务包）+ `convert.py` 转换时自动还原；判断规条写进方针 B6，任何角色看到 `$...$` 里的 `\_` 都应直接还原，不必当作"原文内容"保留。
