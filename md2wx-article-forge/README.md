# md2wx-article-forge

把任意文档转成能直接粘进微信公众号编辑器的排版 HTML。

本项目整体是一个 skill：`SKILL.md` 是给 agent 的操作手册（章节即阶段），
本 README 是给人的详细说明（含设计理由）。两边内容保持一致，以本文件为权威源。

## 快速开始

分步执行（agent 按阶段委派时用的就是这些命令）：

```bash
# 1. 审计 + 改稿基线（内容保真：预算内逐字保留，不删示例与代码块）
python scripts/normalize.py <input.md> --check

# 2. 渲染 + 清洗 + 预览（渲染是纯规则，无 AI 参与）
python scripts/typeset.py <input.md> -o out.html --theme assets/themes/critique.json

# 3.5 整篇公式替换（微信正文不渲染 LaTeX；识别 $...$ 与 $$...$$ 并转图）
python scripts/formula_replace.py article.md -o article_no_formula.md --png-dir .

# 3. 可视化评估：Chrome headless 真实渲染 + DOM 断言 + 375px 分页截图
python scripts/evaluate.py out.html --raw out.raw.html

# 4. LaTeX 公式转图片（单公式调试用；整篇替换见 formula_replace.py）
python scripts/formula_to_png.py "E=mc^2" -o f1.png

# 5. 发布前预演（纯本地检查，不调接口）
python scripts/ship.py out.html --title "标题" --cover cover.jpg --dry-run

# 6. 确认无误后提交草稿箱
python scripts/ship.py out.html --title "标题" --cover cover.jpg
```

主题查重（防止新主题抄了上一篇的配色）：

```bash
python scripts/theme_lint.py assets/themes/new.json
```

内核自检：`python scripts/selftest.py`（36 项断言）

## 多 agent 编排协议

主 agent 只做意图判断、派工、断点检查三件事；每个阶段委派一个全新子 agent，
子 agent 之间通过 `work/` 下的固定产物文件通信（文件存在 = 阶段完成）。
协议细节与产物命名见 `SKILL.md` 的「编排协议」章节。


## 封面图是必填项

`article_type` 为 `news` 时，缺 `thumb_media_id` 接口直接返回
`40007 invalid media_id`。且必须是**永久素材**的 media_id
（`material/add_material` 的返回值），不能用临时素材，也不能用
`uploadimg` 返回的 url。

| 用途 | 接口 | 返回 |
| --- | --- | --- |
| 正文插图 | `media/uploadimg` | `url`（mmbiz 链接） |
| 封面图 | `material/add_material` | `media_id`（永久） |

封面另需满足：JPG、宽度 ≥900px（不足报 53401）、≤2MB、比例 2.35:1。

## 为什么这个项目存在

微信公众号编辑器不接受普通网页 HTML。三条硬约束：

| 约束 | 后果 |
| --- | --- |
| `<style>` 与 class 选择器被剥离 | 必须全部内联样式，不能先写 CSS 再转换 |
| 图片必须挂 `mmbiz.qpic.cn` | 本地路径与第三方 CDN 一律不显示 |
| 外链域名需后台白名单 | 未配置会提示"链接内容不属于当前公众号" |

外加一批过滤规则：`position` / `z-index` / `transform` / `animation` 失效，
`script` / `iframe` / `form` 整节点剔除，`pre` 需改 `pre-wrap`，
表格横向溢出无解（`overflow-x` 随容器标签一起被剥）。

所以核心不是"渲染"，而是**前向规避**——生成时就只产出微信接受的东西。

## 架构

一条流水线，七个阶段，中间产物落盘在 `work/`，每段只读上一段产物：

```
用户消息 → [1 意图判断·主agent] ─┬→ [2 改稿·agent] → 1_normalized.md + 基线
                                │                      ↓
                                │            [3 渲染·脚本] → 2_built.html
                                │                      ↓
                                │            [4 评估·agent+脚本] → 3_eval/
                                │                      ↕（≤3 轮循环）
                                │            [5 修改·agent] → 改 md + 账本
                                │                      ↓
                                └──→ [6 封面] → [7 元数据+发布·agent] → 草稿箱
                                                     （发布前人工确认门）
```

| 阶段 | 谁干 | 脚本干什么 | agent 判什么 |
| --- | --- | --- | --- |
| 1 意图判断 | 主 agent | —— | 有无最终稿/发布信号 |
| 2 改稿 | 子 agent + 脚本 | normalize.py 审计 | 怎么规范化，不删内容 |
| 3 渲染 | 子 agent + 脚本 | typeset.py 渲染+清洗 | 选主题、拆跨行公式 |
| 4 评估 | 子 agent + 脚本 | evaluate.py 量溢出/裂图/留白 | 读分页截图判美感 |
| 5 修改 | 子 agent + 脚本 | triage.py 记账/升级 | 问题归档三档、怎么改 |
| 6 封面 | 脚本（+可选AI图） | make_cover.py | —— |
| 7 发布 | 子 agent + 脚本 | ship.py/wechat_api.py | 拟标题摘要；用户确认后才发 |

**分工原则：凡输入固定则输出固定的环节，都是脚本，不让 AI 用自然语言重跑一遍。**
委派的是阶段，脚本是该阶段子 agent 的工具——脚本给可信的事实底座（量出来的，
不是"看起来的"），agent 的 token 全花在脚本答不了的判断上。
版式角色分配是语义判断，所以 AI 负责决定「这段用什么视觉角色」，脚本只负责
把主题样式盖上去。

## 子 agent 作业手册

每个阶段的子 agent 都是**全新的、零上下文的**。派工时把对应手册整篇交给它，它不需要也不应该知道其他阶段。

| 手册 | 阶段 | 里面有什么 |
| --- | --- | --- |
| `references/briefings/author.md` | 2 改稿 | 角色/内容保真红线/调性判断（tone 写入基线）/视觉锚点写法/组件指令怎么选 |
| `references/briefings/typesetter.md` | 3 渲染 | 公式替换、图片必须同目录（真实事故）、完成判据 |
| `references/briefings/evaluator.md` | 4 评估 | **看图铁律**（只读分页图、严禁 full.png）、issues.json 契约 |
| `references/briefings/fixer.md` | 5 修改 | 三档决策、累积升级、禁止改 .py |
| `references/briefings/cover.md` | 6 封面 | 封面硬规格（JPG/≥900px/2.35:1/≤2MB） |
| `references/briefings/publisher.md` | 7 发布 | mmbiz 上传、**确认门**、错误码对照 |

每份统一十节：角色 → 目标与非目标 → 边界 → 输入契约 → 硬标准 → 执行步骤 → 产出 → 失败怎么办 → 完整示例（真实命令输出） → 回报模板。
改 SOP 请改手册，不要改动 `SKILL.md` 的索引表。

视觉判断的公共依据是 `references/visual-design-brief.md`（组件库、五步流、差异化铁律、完整 md→HTML 案例），
改稿和渲染两端共用同一份。

## 目录

```
SKILL.md                  agent 操作手册（索引：七阶段 + 派工协议）
scripts/
  typeset.py              渲染 + 审计 + 预览（一站式入口）
  normalize.py            语法审计、标题校验、内容保真预算、图片清单
  render.py               markdown → 内联样式 HTML，支持 :::role 组件指令
  wechat_guard.py         HTML → 微信安全 HTML + 违规报告
  wechat_rules.py         兼容性规则表（唯一权威来源）
  evaluate.py             Chrome headless 渲染 + DOM 断言 + 375px 分页截图
  triage.py               评估意见三档分账 + 跨篇累积升级
  formula_to_png.py       单个 LaTeX 公式转 PNG（mathtext，无需系统 LaTeX）
  formula_replace.py      整篇 md 公式识别与图片化替换（行内/块级自动区分）
  make_cover.py           封面生成（需 Pillow）
  upload_images.py        正文图批量上传为 mmbiz 链接 + 就地改写 html（支持 --dry-run）
  ship.py / wechat_api.py 发布前预演、草稿箱 API
  theme_lint.py           主题合规 + 与历史主题查重（Jaccard ≥0.6 拒绝）
  selftest.py             36 项自检
references/               微信限制/评估依据/图片规格/错误码/不兼容语法
  briefings/              六个子 agent 的作业手册（角色/边界/标准/示例/产出）
  visual-design-brief.md  视觉设计基准：组件库、差异化铁律、完整案例
assets/
  themes/                 样式 json（含 components 角色定义）+ registry.json 查重登记
  fixtures/               测试样本
work/                     运行时产物（每篇文章一个子目录 + 跨篇账本 decisions.json）
```

## 改样式

只改 `assets/themes/*.json`，不要动代码。字号建议正文 15–17px，
小于 14px 真机难读。

## 边界

- `draft/add` 默认只写草稿箱，不群发。群发受订阅号每日 1 次 / 服务号每月 4 次限制
- 群发需 `freepublish/submit`，新号往往无权限，返回 `48001`
- 2025-07 起未认证主体已被回收发布接口权限
- 标题层级上限 3 级：h4–h6 不可靠，且 375px 屏幕上超过 3 级已无法视觉区分；
  超出部分渲染器降级并在 `heading_warnings` 里告警，绝不静默
- 容器标签口径：`<section>` 支持，`<div>` 样式会被清除 —— 渲染器统一输出 section，
  清洗器把 div 改写为 section 而不是丢弃
- 数学公式不会渲染，需服务端转图片
- 评估用本机 Chrome/Edge 的 headless CLI（Windows 下最小窗口宽约 500px，
  脚本用定宽容器 + 裁剪绕开），不需要 playwright
- Pillow 仅封面生成与截图裁剪需要；核心渲染链纯 stdlib

## 参考

- [新增草稿](https://developers.weixin.qq.com/doc/service/api/draftbox/draftmanage/api_draft_add.html)
- [服务端 API 总览](https://developers.weixin.qq.com/doc/service/api/)
- [微信 HTML/CSS 支持概览](https://www.axtonliu.ai/newsletters/ai-2/posts/wechat-article-html-css-support)