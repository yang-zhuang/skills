# scripts/ — 应用筛选脚本

## 运行环境

- Python 3.10+，**只用标准库**，不需要 pip install。
- 大模型部分依赖本机 `agnes-key-pool` 代理，默认 `http://127.0.0.1:8765/v1`，模型 `agnes-3.0-flash`。
  可用环境变量改：`AUTODL_ART_LLM_BASE_URL`、`AUTODL_ART_LLM_MODEL`、`AUTODL_ART_LLM_KEY`。
- 陈旧闸门：`AUTODL_ART_STALE_DAYS` 默认 180（超期应用在进大模型前剔除），
  `AUTODL_ART_AGING_DAYS` 默认 90（超期但未过闸门的要在理由里提示维护偏旧）。
- 两阶段筛选：`AUTODL_ART_COARSE_TOP_N` 默认 30（阶段1 粗选数量），
  `AUTODL_ART_DEEP_README_LIMIT` 默认 5000（阶段2 发给大模型的 README 截断长度）。
- 所有路径按 `Path(__file__)` 推导；产物统一写到 `../outputs/`，该目录不入库。
- Windows 下若终端中文乱码，命令前加 `PYTHONIOENCODING=utf-8`。

## 脚本矩阵（全部是位置参数，没有 `--` 选项）

| 脚本 | 用法 | 做什么 | 产物 | 风险 |
|---|---|---|---|---|
| `inspect_api.py` | `python inspect_api.py` | 只读探查列表/详情/标签/周榜四个接口并打印字段 | `outputs/probe/*.json` | 🟢 只读 |
| `check_proxy.py` | `python check_proxy.py` | 检查代理健康 + 发一句 "hi" 验证模型 | 无 | 🟢 无业务请求 |
| `fetch_apps.py` | `python fetch_apps.py [每页条数] [标签id]` | 按默认顺序抓全部应用；默认 100/页，自动翻页 | `outputs/apps.json`、`outputs/tags.json` | 🟢 只读，限速 |
| `fetch_details.py` | `python fetch_details.py [个数]` | 按列表顺序逐条抓详情；默认 30，`0` = 全部；已抓过的跳过 | `outputs/app_details.json` | 🟢 只读，限速 |
| `select_apps.py` | `python select_apps.py [需求] [TopN]` | **两阶段**：阶段1 列表字段粗筛 30 个 → 阶段2 README 全文深判出前 N；默认"视频生成"/前 5 | `outputs/coarse_<需求>.json`、`outputs/selection_<需求>.json`、`outputs/llm_raw_coarse_<需求>.txt`、`outputs/llm_raw_deep_<需求>.txt` | 🟢 只调用本地代理 |
| `report.py` | `python report.py [需求]` | 把排序结果渲染成 Markdown 筛选报告；导出过封面时排名表直接显示封面图 | `outputs/shortlist_<需求>.md` | 🟢 纯本地渲染 |
| `export_apps.py` | `python export_apps.py [个数]` | 分层导出：每个应用一个文件夹（封面/头像/readme.md 本地化/README 图片/版本 info），断点续跑 | `outputs/apps/<id>_<名称>/` | 🟢 只读，限速 |
| `config.py` | 被 import | 接口地址、限速、代理地址、陈旧闸门、粗筛/深判参数、输出目录 | — | ⚪ 库 |
| `api_client.py` | 被 import | JSON 请求、随机间隔、失败重试 | — | ⚪ 库 |
| `prompt.py` | 被 import | 加载 prompt + 摘要/提示词构造 | — | ⚪ 库 |
| `prompts/coarse_system.md` | 编辑 | 阶段1 粗筛 system prompt（宁多勿漏） | — | ✏️ 文档 |
| `prompts/filter_system.md` | 编辑 | 阶段2 深判 system prompt（README 全文逐条核对） | — | ✏️ 文档 |

## 典型流程

```bash
cd <本 skill 目录>/scripts
python inspect_api.py
python check_proxy.py
python fetch_apps.py
python fetch_details.py 0            # 两阶段深判要 README 全文，建议抓全（约 4~10 分钟）
python select_apps.py "文生视频，3090 能跑" 5
python report.py "文生视频，3090 能跑"   # 生成 shortlist_文生视频，3090 能跑.md
python export_apps.py                # 可选：导出封面/README/媒体，报告里就能看到封面
```

为什么两阶段：243 个应用的 README 全文一次发给大模型约 40 万字，会超上下文且稀释判分；
先用列表字段（名称/简介/标签/精热/热度）粗筛 30 个，再对入选者发 README 全文逐条核对，又深又省。

`run_pipeline.sh` 是命令备忘，不会自动执行。

## 常见问题

- **「找不到 apps.json」** → 先跑 `python fetch_apps.py`。
- **「大模型调用失败」** → 到 agnes-key-pool 启动代理服务；`python check_proxy.py` 复验。
- **「解析大模型 JSON 失败」** → 粗筛看 `outputs/llm_raw_coarse_<需求>.txt`、深判看 `outputs/llm_raw_deep_<需求>.txt` 原文定位；多为模型超时截断，重跑即可。
- **结果不理想** → ①先看详情覆盖率提示，补 `fetch_details.py 0`；②把需求写得更可判定（见 `../references/filter-criteria.md`）；③调整 `prompts/filter_system.md` 的排序标准。

## 边界

本目录**没有**任何登录、选 GPU、下单、抢机、开关机的代码。筛选出的应用要部署，
去 `autodl-art-rent-bot`（开发者 token / 网页 JWT / 抢机都在那边）。
