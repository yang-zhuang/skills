---
name: autodl-art-app-filter
description: 在 AutoDL Art 应用广场按自然语言需求筛选应用。零登录抓取匿名公开接口拿到全部应用列表与详情（名称、作者、README 全文、版本、精/热标记、下载量、更新时间），采用两阶段筛选：阶段1 用列表字段粗筛约 30 个，阶段2 对入选者用 README 全文逐条核对需求条件（显存、卡型、排除项）选出 TopN，硬规则含精/热标记、陈旧闸门（默认 180 天剔除）；可另用 export_apps.py 分层导出封面/头像/README/媒体，筛选报告直接嵌入本地封面。This skill should be used when 用户想在 autodl.art 应用广场找应用、按需求/关键词筛选应用、让大模型推荐最合适的应用、生成候选清单或筛选报告、导出应用资料。关键词：AutoDL Art、autodl.art、应用广场、应用市场、应用筛选、大模型择优、两阶段筛选、TopN、筛选报告、视频生成、TTS。边界：本 skill 只筛选不部署——不登录、不选 GPU、不下单、不开关机；那些归 autodl-art-rent-bot。
agent_created: true
---

# AutoDL Art 应用筛选（autodl-art-app-filter）

**目的先行**：你说一句需求，我给你一份有证据的候选清单，你照着挑。整条链零登录：调的都是应用广场网页自己用的匿名公开接口。

## 与其他 skill 的分工

| | 本 skill `autodl-art-app-filter` | `autodl-art-rent-bot` |
|---|---|---|
| 目标 | **筛选**：按需求出 TopN 候选 + Markdown 报告 | 全链路：抓取→择优→终选→**选卡→抢机→开关机** |
| 凭据 | 无（纯匿名接口 + 本地大模型代理） | 开发者 token + 网页 JWT |
| 花钱动作 | **一个都没有** | create/power_on（有人工闸门） |

只想要候选清单就用本 skill；选完要部署，去 rent-bot。

## 决策树

```
想做什么？
├─ 接口/字段体检（改版后先跑这个）
│   └─ python inspect_api.py
├─ 验证本地大模型代理
│   └─ python check_proxy.py
├─ 抓数据
│   ├─ python fetch_apps.py                 # 全部应用 + 标签树（约 1 分钟）
│   └─ python fetch_details.py 0            # 抓详情；30/0=全部，可断点续抓（建议 0，深判要 README 全文）
├─ 筛选（两阶段：列表字段粗筛 30 → README 全文深判前 N）
│   └─ python select_apps.py "文生视频，3090 能跑" 5
├─ 出报告（导出过封面的话排名表直接显示封面图）
│   └─ python report.py "文生视频，3090 能跑"   # shortlist_<需求>.md
└─ 可选：分层导出封面/头像/README/媒体（outputs/apps/<id>_<名称>/，断点续跑）
    └─ python export_apps.py
```

## 不可违背的硬规则

1. **只筛选，不部署**：不登录、不碰 GPU 规格/下单/开关机接口。要部署用 `autodl-art-rent-bot`。
2. **需求必须是自由文本但要有可判定的条件**：「文生视频，3090 能跑」是好需求；「帮我找个好用的」不是——模型只能按热度兜底。
3. **筛选标准只改 prompts/ 下的 Markdown**：阶段1 粗筛在 `coarse_system.md`（宁多勿漏），阶段2 深判在 `filter_system.md`（README 全文逐条核对）；粗筛数量与 README 截断长度在 `config.py`（`AUTODL_ART_COARSE_TOP_N`、`AUTODL_ART_DEEP_README_LIMIT` 可临时改）。
4. **硬规则在代码不在嘴上**：精/热 badge、更新时间取应用级 `updated_at`（不是 `latest_version_updated_at`）、超 180 天的应用在进大模型**之前**由代码剔除并记录在 `excluded_stale`——不许静默消失。
5. **证据落地**：大模型原文单独存（粗筛 `llm_raw_coarse_<需求>.txt`、深判 `llm_raw_deep_<需求>.txt`），解析失败能直接看原文；深判理由里必须带「精/热/无、更新多久、下载量」至少两项真实数值。
6. **详情覆盖率必须提醒**：粗筛入选的应用若没有 README 详情，脚本会主动警告（只有列表字段、深判依据弱），报告里也会标注。
7. **产物不入库**：`outputs/` 全量忽略。
8. **限速**：请求间隔 1~2.5 秒随机；失败最多重试 2 次；长任务逐行输出进度与预计剩余时间。

## 快速开始

```bash
cd <本 skill 目录>/scripts
# 无第三方依赖；Python 3.10+。大模型走本机 agnes-key-pool 代理（127.0.0.1:8765）
python inspect_api.py                        # 1. 体检（只读）
python check_proxy.py                        # 2. 大模型代理连通性
python fetch_apps.py                         # 3. 抓全部应用 + 标签树
python fetch_details.py 0                    # 4. 抓全部详情（约 4~10 分钟，深判要 README 全文）
python select_apps.py "文生视频，3090 能跑" 5 # 5. 两阶段筛选 TopN
python report.py "文生视频，3090 能跑"        # 6. 生成 shortlist_<需求>.md
python export_apps.py                        # 7. 可选：导出封面/头像/README/媒体，报告显示封面
```

`scripts/run_pipeline.sh` 是命令备忘，不会自动执行。

## 需求写法（正反例）

- ✅ `文生视频，3090 能跑` —— 有用途 + 硬件条件
- ✅ `中文 TTS 语音克隆，要能离线跑` —— 有用途 + 约束
- ✅ `ComfyUI 工作流，做电商产品图` —— 有载体 + 场景
- ❌ `找个好用的` —— 无可判定条件，只能按热度兜底
- ❌ `视频` —— 太泛，等于没筛

## 参考文件

- `scripts/README.md` — 每个脚本用法、产物、排错。
- `scripts/prompts/coarse_system.md` — 阶段1 粗筛标准全文（宁多勿漏）。
- `scripts/prompts/filter_system.md` — 阶段2 深判标准全文（README 全文逐条核对），改需求只改它。
- `scripts/prompt.py` — 加载 prompt 并把应用字段压成摘要的构造函数。
- `scripts/export_apps.py` — 分层导出封面/头像/README/媒体（与 rent-bot 同款，本包独立自包含）。
- `references/anonymous-api.md` — 接口发现过程、字段字典、限速、改版排查。
- `references/filter-criteria.md` — 需求怎么写、筛选标准的设计依据与正反例。
