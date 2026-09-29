---
name: dwg-split-pipeline
description: 把含多张示意图的 DWG/DXF 按空间连通聚类拆成一张张单图（一图一个序号目录 + dxf/json/两配色 PNG），并对拆分结果做「拆分前 vs 拆分后」的视觉对照与确定性校验，发现问题就改拆分代码再迭代。只处理已经是天正3（T3）格式的图：源图没导出成 T3 会被自动判型拦下并提示回天正导出（本管线不做转换）。当用户说"把这张总图拆开""这张 dwg 里有多张图要分开""检查一下拆分结果对不对""拆出来的图有问题""为什么拆错了"时使用。关键词：DWG/DXF 拆分、天正3、T3、空间连通聚类、序号目录、整图对照、确定性校验、多 agent 闭环、拆分代码迭代。实测于含多张示意图的房屋检测总图（天正3 导出图，管线自动判型后拆出 5~6 张；纯 CAD 图同样适用）。
agent_created: true
---

# DWG 拆图流水线

一张 DWG 里混着好几张图（多层平面、倾斜观测、总平面…）。这个 skill 把它们拆成
**一图一个序号目录**，每张配 dxf + json + 两张配色 PNG，再用「拆分前整图 vs 拆分后单图」
的视觉对照来判断拆得对不对，不对就改代码重跑，直到评估通过。

**核心纪律：凡是确定性的动作，一律是 `python xxx.py 参数`。**
不要让 agent 自主发挥去"看着办"—— 转格式、渲染、拆分、校验都是程序；agent 只干两件事：
**判断**（图拆对没有）和**改代码**（不确定时）。

**核心边界：只处理已经是天正3（T3）格式的图。** 程序读图后自动数代理对象
（`ACAD_PROXY_ENTITY`）判型：有就说明**没导出成 T3**，直接拦下并提示用户回天正
导出一次（天正命令行 `TSaveAs` / 菜单『图形导出』→ 保存类型选『天正3』，几秒钟的事）。
**本管线不做 DWG → T3 转换** —— 调用天正 GUI 自动化既难做又难验，交给人点一次更可靠
（2026-09-23 定；详见 README「拆分对象」）。`--allow-proxy` 可照拆未导出的图（产物缺几何）。

## 一、硬前置：装依赖 + 两个外部依赖，缺一个就别往下走

**第一条命令要在 skill 的 `scripts/` 目录里跑** —— `requirements.txt` 和 `.env` 都在那儿：

```bash
cd <skill 目录>/scripts
pip install -r requirements.txt    # ezdxf（读图）+ matplotlib（出图）
python check_env.py
```

| 依赖 | 缺了会怎样 |
|---|---|
| ODA FileConverter | 读不了 dwg（dxf 可绕过）。装好并在 `.env` 里配 `ODA_EXE` |
| SHX 字体目录 | **不报错**，但轴号/尺寸数字会发虚、字形变空心 —— 静默降级最难查 |

`scripts/.env` 已经给好了两项的配置样板（**含作者本机的路径，没有密钥**）。换机器就把
路径改成你自己的，然后跑 `python check_env.py` 验 —— 路径不对会直接报 `✗` 并退出码 1，
不会静默降级。

## 二、作业目录结构（**固定，不要自己发明**）

一个**源图**一个作业单元；同一个作业根下可以有多个源图，各自一套，结构完全一样。
每个环节的产物都落在**确定的位置** —— 子 agent 拿到路径直接读，不必搜索，省时间也省 token。

```
<作业根>/
├─ _batch.json                    这一轮有哪几个单元、各自什么状态
├─ <源图主干>/                      ← 一个作业单元 = 一张源图
│   ├─ state.json                 轮次 / 当前环节（主 agent 的账本）
│   ├─ 01_source/                 源图（只读，永不改写）
│   ├─ 02_overview/               **拆分前**整图 PNG × 2  ← 视觉对照的基准
│   ├─ 03_split/                  拆分产物：01/ 02/ …（dxf + json + 2 PNG）+ split_report.json
│   ├─ 04_verify/校验报告.json     确定性校验输出（脚本产）
│   ├─ 05_review/视觉意见.json     视觉评估输出（评估 agent 产）
│   ├─ 06_work/                   base 副本 + BASE.txt 指纹 ← 要改代码就改这里
│   └─ 07_notes/                  NOTES.md（本张图的坑）+ round-N.diff（改动）
└─ <另一个源图>/                    （同上）
```

固定结构的三个理由，改之前先想清楚：

1. **编号 = 流程顺序** —— 和拆分产物用 `01/` `02/` 是同一套思路：顺序即事实，不用猜。
2. **一源一单元**：整图一源一张、报告一源一份、代码副本**必须一源一份**
   （见第四节，改动不能跨图互相污染）。
3. **`06_work/` 独立于 `01_source/` 与 `03_split/`**：代码与图纸、产物分开，谁都不会误改谁。

> ⚠️ 不要把作业开在 skill 包内部（如 `assets/`）。skill 装到别人机器上时，
> 往程序目录里写图纸与产物，升级/卸载会连作业一起动。

## 三、搭作业：一条命令

**同样在 `scripts/` 里跑**（它会自动把同目录的 `.env` 一起带进现场）：

```bash
python init_task.py "<作业根>" "<源图1.dwg>" "<源图2.dxf>" …
```

它会：建 7 个编号目录 → 把源图复制进 `01_source/`（**不移动原件**）→ 把 base 复制到
`06_work/`（含 `.env`）→ 写 `BASE.txt` 指纹 → 写 `state.json` 与 `NOTES.md` 模板。

重复运行安全：已存在的文件不覆盖，只补缺失的。**它不做任何删除**。
（注意：一个作业根下**源图主干不能重名** —— 单元目录以主干命名，重名会被拦下。）

## 四、五个环节：每步一条命令

在 `06_work/` 里执行（现场那份代码副本，见下节）：

```bash
cd <作业根>/<源图主干>/06_work

# 前置（人工做）：把源图在**天正**里导出成天正3（T3），放进 01_source/
#   本管线不做转换 —— 没导出的图会在下面第 1 步被直接拦下并提示

# 1 整图（**拆分前**的基准）—— 必须与拆分源同口径（同一张文件）
python main.py ../01_source/<源图>.dwg --overview --out=../02_overview

# 2 拆分
python main.py ../01_source/<源图>.dwg --out=../03_split --clean --title-mode=auto

# 3 确定性校验（零 LLM）
python verify_split.py ../03_split ../01_source/<源图>.dwg --out=../04_verify

# 4 视觉评估      → 派只读子 agent，读 02_overview/ 与 03_split/ 的 PNG，写 05_review/视觉意见.json
# 5 改代码并重跑  → 派可写子 agent，只改 06_work/，然后回到第 2 步
```

`--title-mode=auto` 是默认推荐：图名（轴网下方那行大字）常比图形本身还大，
渲出来会把图形挤成一小块；auto 在识别到图名时剔除它（指北针、尺寸线一律保留）。

## 五、子 agent 怎么派、权限怎么给

**交接一律走文件**（子 agent 之间没有直接通道，只能经主 agent 转达）。
主 agent 只读判断结论与条数，**不要把评估意见全文搬进自己的上下文** ——
一旦多张图多轮迭代，主 agent 会先爆。

| 角色 | 权限 | 读什么 | 写什么 |
|---|---|---|---|
| 主 agent | — | `state.json`、两份报告的**摘要** | `state.json`、`07_notes/round-N.diff` |
| 评估 agent | **只读**（不给写权限） | `02_overview/*.png`、`03_split/*/*.png`、`04_verify/校验报告.json` | `05_review/视觉意见.json` |
| 修改 agent | 只允许写 `06_work/` | `05_review/视觉意见.json`、`04_verify/校验报告.json`、`06_work/` 源码 | 改代码 + 追加 `07_notes/NOTES.md` |
| 提炼 agent | 只允许写 `references/` | `07_notes/NOTES.md` + `round-N.diff` | 往 `references/LESSONS.md` 追加条目 |

参数化提示（不同宿主写法不同，核心是**按工具授权**，不是靠 agent 自觉）：
- WorkBuddy：评估 agent 用只读模式（plan 类），修改 agent 用可写模式（acceptEdits 类）；
- 其他宿主不支持按工具授权时，就在 prompt 里写死"你只能写 X 这一个路径，其余一律不许写"。

**每个子 agent 都要用全新的**（不要 resume 上一次的）—— 防上下文污染。
代价是它不知道上一轮改了什么，所以派活时**必须把 `07_notes/round-N.diff` 和
`05_review/` 里的历史结论一起给它**，并明确要求"已经判定为已修复的问题不要重复提，除非你能复现"。

## 六、闭环与终止

```
0 转T3 → 1 整图 → 2 拆分 → 3 脚本校验 ─┬─ 有 error → 5 改代码 → 回 2
                                        └─ 无 error → 4 视觉评估 ─┬─ 通过 → 收敛
                                                                 └─ 有问题 → 5 改代码 → 回 2
```

**终止条件（三条任一命中就停，不要无限转）**：

1. 评估给出 `verdict = pass`；
2. 轮次到达上限（**默认 3 轮**）；
3. **问题集合连续两轮无变化** → 判为 `stuck`，停下交人 —— 说明修改 agent 改不动了，
   再转下去只是烧 token。

评估意见必须**可证伪**。"图切得不太好"是无效结论；有效结论长这样：
"`03_split/02` 里混进了 `03` 的标题栏"、"`05/05.dxf` 只有轴线没有墙体"、
"`04/` 里的图名文字没剔掉"。判据清单见 `references/EVALUATION.md`。

## 七、代码怎么改（**只改现场副本，不回流主仓**）

- `06_work/` 是从**干净 base**（skill 的 `scripts/`）复制出来的一份，**改它、只跑它**。
- 改完用 `python compare_work.py <作业单元目录>` 算出相对 base 改了哪些文件、哪些行，
  输出到 `07_notes/round-N.diff` —— 不依赖 git，靠与干净 base 逐行比对。
- **改动不回流主仓**。经验靠文档沉淀（见下），代码副本是消耗品。
- 每个新作业都从干净 base 重新复制 —— 所以**上一张图的改动绝不会影响下一张图**。

沉淀路径：本张图的坑 → `07_notes/NOTES.md`；收敛后由提炼 agent 归纳成**配方**
（现象 → 判据 → 改哪个文件哪一行 → 改成什么）→ 追加进 `references/LESSONS.md`。
下次遇到同类问题，直接照配方改，不必重新摸索。

> `references/LESSONS.md` 里的条目都带 base 指纹。base 变过之后，靠
> `BASE.txt` 里的 sha256 判断某条老经验还适不适用。

## 八、失败模式速查

| 现象 | 真正的原因 |
|---|---|
| 拆出来只有轴线 + 图名 + `‰`，墙体门窗全没有 | 源图没导出成天正3（T3）—— 天正对象几何只有天正能解析（见 README「拆分对象」）。这是**源格式问题**：回天正导出一次 T3 再来拆，或用 `--allow-proxy` 明知缺内容照拆 |
| `需先在天正导出天正3` 且一个产物都没有 | 源图带着读不到几何的天正对象。这是**源格式问题，不是失败**：按提示在天正里导出一次，再跑同一条命令（断点续传不会跳过它） |
| 图渲出来发虚、字变空心、轴号撑破圆圈 | SHX 字体没注册上（`SHX_FONT_DIRS`）。见 `references/RENDERING.md` |
| 只有数字/轴号发虚（中文却清晰），加粗无效 | 字体被静默换成 TTF 替身（填充路径不读线宽）。`check_env.py` 会点名缺哪几个 —— 通常是 AutoCAD 装在非 Program Files，默认通配匹配不上，把实际 Fonts 目录补进 `SHX_FONT_DIRS`（2026-09-23 实战：CAD 在 `G:\CAD2021`，`C:\Program Files\Autodesk` 通配永远落空） |
| 拆出来的图里"缺了一大块" | 先看 `04_verify/校验报告.json` 的实体数守恒；不守恒才是真丢 |
| 一张图里还挤着两张图 | `--margin` 调小；`校验报告.json` 的 `multi_component` 会报出间隙占比 |
| 出现 4 万+实体的巨簇 | `margin=3000` 对互相连通的图切不开，见 `references/LESSONS.md` |
| 产物目录里有 `[]`（0 张） | 空图源（12,991 字节的空壳），**不是失败** |
| `titles` 有值但 `title_entities=0` | 命名与剔除口径跑偏，`--title-mode` 会静默失效 |

## 九、`scripts/` 里有什么：谁在什么时候用

六个环节**只依赖下面 11 个**（5 个能直接跑，6 个是被 import 的库）：

| 文件 | 怎么用 | 什么时候用到 |
|---|---|---|
| `check_env.py` | CLI | 开工前自检 ODA / SHX 字体两项依赖 |
| `init_task.py` | CLI | 搭作业（一个作业只跑一次） |
| `main.py` | CLI 主入口 | 整图 / 拆分 / 重渲 / 探查，四种模式都用它 |
| `verify_split.py` | CLI | 拆分后的确定性校验 |
| `compare_work.py` | CLI | 改过现场代码后，算改动（不依赖 git） |
| `split_dwg.py` | 库 | 被 `main.py` / `split_and_render.py` import（拆分与聚类都在它里面） |
| `png_export.py` | 库 | 被上面几乎每个文件 import（渲染 / 字体 / SHX 注册都在这） |
| `split_and_render.py` | 库 | `main.py` 从它 import `run_target`（目录级批量）；**不必单独跑** |
| `render_png.py` | 库 | `main.py` 从它 import `render_dir`（重渲）；**不必单独跑** |
| `env_file.py` | 库 | 被 `main.py` / `check_env.py` / `verify_split.py` import，读 `.env` |
| `cluster_dwg.py` | 库 | `main.py --cluster` 从它 import（**函数体里 import，见下面那条坑**）；**别单独跑** |

> ⚠️ `cluster_dwg.py` 必须和主程序放在**同一层**。`main.py` 的 `--cluster` 分支是
> 在函数体里 `import cluster_dwg` 的（`main.py:99`）—— 把它挪走，`--cluster` 直接
> `ModuleNotFoundError`。**只 grep 行首的 `import` 会把这类依赖漏掉。**
>
> ⚠️ 也**别单独手工跑** `cluster_dwg.py`：它不注册 SHX 字体、文字量不出真实 bbox，
> 报出来的簇数对不上真实拆分。要探查就用 `main.py --cluster`。

`scripts/extras/` 是**参考代码库** —— 流程不依赖它，但两个脚本各解决过一类具体问题，
写法本身是现成思路（详见 `scripts/extras/README.md`）：

| 文件 | 解决的那类问题 | 可借鉴的写法 |
|---|---|---|
| `batch_t3.py` | 一个根下几百个 `t3` 文件夹，各拆一套 | 断点续传标记、`os.walk` 原地裁剪、`tqdm` 兜底 import |
| `merge_permille.py` | 数字与 `‰` 是两个实体、渲染后叠在一起 | 按距离与字高配对后合并成一个 `TEXT` |

另外三个非 `.py` 文件：`scripts/.env`（三项依赖的配置样板，换成你机器的路径后跑
`check_env.py` 验）、`scripts/requirements.txt`（依赖清单：`ezdxf` + `matplotlib`）、
`scripts/BASE.txt`（本包脚本的 sha256 指纹，**别手改**）。

> ⚠️ **发行版 `scripts/` 是只读基线。** 要改流程代码请改现场 `06_work/` 里的副本 ——
> 改了这里，`compare_work.py` 与 `BASE.txt` 的比对就失去意义了。
>
> ⚠️ 主仓（开发区）的 `scripts/*.sh` 是作者自己的命令备忘（python 路径写死在本机），
> **不在发行版里**。别照抄，按第四节那几条命令敲即可。

## 十、参考文档（按需读，别一次全读）

| 文件 | 什么时候读 |
|---|---|
| `references/EVALUATION.md` | 评估 agent 必读：怎么算"拆成完成"，逐条判据 |
| `references/LESSONS.md` | 改代码前先翻：这类问题以前踩过没有 |
| `references/RENDERING.md` | 图渲出来不对（发虚/空心/缺字）时 |
