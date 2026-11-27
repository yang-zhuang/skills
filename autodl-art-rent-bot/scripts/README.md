# scripts/ — 抓取、择优与官方 API 部署脚本

## 运行环境

- Python 3.10+，**只用标准库**（urllib / json / pathlib），不需要 pip install。
- 大模型部分依赖本机 `agnes-key-pool` 代理，默认 `http://127.0.0.1:8765/v1`，模型 `agnes-3.0-flash`。
  可用环境变量改：`AUTODL_ART_LLM_BASE_URL`、`AUTODL_ART_LLM_MODEL`、`AUTODL_ART_LLM_KEY`。
- 部署部分依赖包根 `.env` 里的 `AUTODL_DEV_TOKEN`（开发者 token，`.env` 不入库）。
- 陈旧闸门：`AUTODL_ART_STALE_DAYS` 默认 180（超期应用在进大模型前剔除），`AUTODL_ART_AGING_DAYS` 默认 90（超期但未过闸门的要在理由里提示维护偏旧）。
- 所有路径按 `Path(__file__)` 推导；产物统一写到 `../outputs/`，该目录不入库。
- Windows 下若终端中文乱码，命令前加 `PYTHONIOENCODING=utf-8`。

## 脚本矩阵

前半段（抓取/择优）是位置参数；后半段（部署）用 `--` 选项，因为参数多、容易看混。

| 脚本 | 用法 | 做什么 | 产物 | 风险 |
|---|---|---|---|---|
| `inspect_api.py` | `python inspect_api.py` | 只读探查列表/详情/标签/周榜四个接口并打印字段 | `outputs/probe/*.json` | 🟢 只读 |
| `check_proxy.py` | `python check_proxy.py` | 检查代理健康 + 发一句 "hi" 验证模型 | 无 | 🟢 无业务请求 |
| `fetch_apps.py` | `python fetch_apps.py [每页条数] [标签id]` | 按默认顺序抓全部应用；默认 100/页，自动翻页 | `outputs/apps.json`、`outputs/tags.json` | 🟢 只读，限速 |
| `fetch_details.py` | `python fetch_details.py [个数]` | 按列表顺序逐条抓详情；默认 30，`0` = 全部；已抓过的跳过 | `outputs/app_details.json` | 🟢 只读，限速 |
| `select_apps.py` | `python select_apps.py [标题] [TopN]` | 大模型按标题排序，默认“视频生成”/前 5 | `outputs/selection_<标题>.json`、`outputs/llm_raw_<标题>.txt` | 🟢 只调用本地代理 |
| `choose_final.py` | `python choose_final.py [标题]` | 打印候选，人工输入最终 ID；回车取消 | `outputs/final_choice.json` | 🟢 只写本地文件 |
| `dev_check.py` | `python dev_check.py` | 验证开发者 token + 列账号下实例 | 无 | 🟢 只读 |
| `select_gpu.py` | `python select_gpu.py` | 打印 6 个规格 ID ↔ 网页显示名对照 | 无 | 🟢 只读 |
| `deploy.py` | `python deploy.py --app-id 273 --gpu 5090-p --gpu-amount 1 [--dry-run\|--yes]` | 创建实例；**默认 dry-run 不发请求**，加 `--yes` 才花钱 | 无 | 🔴 会花钱 |
| `grab.py` | `python grab.py stock 西北B区` / `power <uuid>` / `create --app-id 273 --gpu 5090-p,pro6000-p [--signal]` | 查库存 / 抢开机 / 抢创建；`--signal` 有货才点（对齐 autodl）；默认 dry-run | `outputs/grab_*.log` | 🟠 长时间轮询；create 模式会花钱 |
| `export_apps.py` | `python export_apps.py [N]` | 把详情导出成「一应用一文件夹」：`outputs/apps/<id>_<名>/` 下放 `info.json`（全字段）、`readme.md`（图片改写为本地路径）、`cover.*`（封面）、`avatar.*`、`media/`（README 图片按顺序编号）；断点续跑、限速 | `outputs/apps/` | 🟢 只读+下载图片 |
| `extract_jwt.py` | `python extract_jwt.py --login`（第一次）→ 之后直接 `python extract_jwt.py` | 自动提取网页登录 JWT：无头试读 profile，读不到才弹窗口让你登录一次；写入 `.env` 的 `AUTODL_ART_WEB_JWT` | `.env` | 🟢 只读 localStorage，不碰账号密码 |
| `web_api.py` | 被 import | 网页登录态客户端（库存查询 `machine_pro/gpu_type`） | — | ⚪ 库 |
| `power_ctl.py` | `python power_ctl.py list\|status\|snapshot\|on\|off\|release <id>` | 实例状态与开关机；`release` 需 `--yes` | 无 | 🟠 改状态；release 不可逆 |
| `config.py` | 被 import | 接口地址、限速、代理地址、token、规格表、输出目录 | — | ⚪ 库 |
| `api_client.py` | 被 import | 公开接口 JSON 请求、随机间隔、失败重试 | — | ⚪ 库 |
| `dev_api.py` | 被 import | 开发者 API 调用（token 鉴权，不碰登录表单） | — | ⚪ 库 |
| `prompt.py` | 被 import | 加载 prompt + 摘要/提示词构造 | — | ⚪ 库 |
| `prompts/select_system.md` | 编辑 | system prompt 全文（挑选标准都在这里） | — | ✏️ 文档 |

## 推荐跑法（每一步都看输出再进下一步）

```bash
cd <本 skill 目录>/scripts
python inspect_api.py
python check_proxy.py
python fetch_apps.py
python fetch_details.py 30
python select_apps.py "视频生成" 5
python choose_final.py "视频生成"

# 部署阶段（先在包根 .env 放 AUTODL_DEV_TOKEN）
python dev_check.py
python select_gpu.py
python deploy.py --app-id 273 --gpu 5090-p --gpu-amount 1 --dry-run
python deploy.py --app-id 273 --gpu 5090-p --gpu-amount 1 --yes
python power_ctl.py list
```

`run_pipeline.sh` 把抓取那段命令按顺序写在一起，只是备忘，不会自动执行。

提示：`select_apps.py` 会打印「详情覆盖率」。只抓 30 个详情时，其余应用只有列表字段、没有 README 证据；
想让全部 243 个都参与严格评估，先跑 `python fetch_details.py 0`（约 4~10 分钟）再择优。

## 抢机器（`grab.py`）

autodl.art 官方 API **没有库存信号**，所以不存在 autodl-rent-bot 那种「看到 `.gpuTips` 才点」的精准抢法。
只能盲试，效果一样、代价是会有若干次无效请求。

| 模式 | 适用 | 做什么 |
|---|---|---|
| `stock <地区,...>` | 开始前看行情 | 查各地区各卡型空闲/总数（只读；需网页 JWT） |
| `list` | 开始前先看有哪些实例 | 列出全部实例 uuid 和状态（只读） |
| `power <uuid>` | 实例已存在、处于关机，想等资源开机 | 反复调 `power_on`，成功且状态变 running 即停 |
| `create --app-id N --gpu X[,Y]` | 还没实例，想抢一台新的 | 反复调 `create`，建出实例即停；`--gpu` 支持逗号分隔多个规格轮换（"给定规则"），任何一个先建成功就停 |
| `create ... --signal --region 西北B区,北京B区` | **推荐**：有货才点 | 每轮先查库存（`gpu_idle_num`），有空闲卡才发 create；无货只等待。效果对齐 autodl 的 `.gpuTips`，无效 create≈0。前提：先跑 `python extract_jwt.py --login` 登录一次 |

```bash
python extract_jwt.py --login                                               # 一次性：登录，之后全自动
python grab.py stock 西北B区,北京B区                                        # 看库存行情（只读）
python grab.py list                                                         # 看已有实例
python grab.py create --app-id 273 --gpu 5090-p,pro6000-p --region 西北B区,北京B区 \
        --signal --hours 12 --dry-run                                       # 先看计划
python grab.py create --app-id 273 --gpu 5090-p,pro6000-p --region 西北B区,北京B区 \
        --signal --hours 12 --yes                                           # 真抢（有货才点）
```

关键设计（防风控是硬编码，不可关）：

- **永远单线程、单账号、串行请求**，绝不并发；每次调用之间强制 ≥1 秒间隔。
- **间隔默认 60 秒 + ±25% 随机抖动**，下限 30 秒；不要设成几秒，那是风控最敏感的模式。
- **连续失败 ≥15 次自动退避**：等待时间逐次 ×1.5，最多放大到基础间隔的 4 倍；成功或错误类型变化即重置。失败越多越安静，绝不加速重试。
- **致命错误立刻停**：token 无效（AuthorizeFailed）、参数错、余额不足抛 `FatalDevApiError`，脚本直接退出非 0，绝不带着坏状态硬抡。可重试错误（如 RecordNotFoundError）才继续轮询。
- **create 模式防重复**：每次失败后先比对实例列表，若出现基线之外的新实例，说明上次其实建成功了，
  立即停止，避免重复计费。
- **登录零参与**：凭据只有 `.env` 里的开发者 token（你在控制台手动生成），脚本不碰浏览器、不碰登录表单、不碰验证码，不需要任何扫描登录步骤。
- 全程写 `outputs/grab_*.log`，逐行 flush，Ctrl+C 随时停，重启不丢历史。
- 打印每次尝试的时间、结果、剩余时长。

## 部署阶段的规矩

- **token 只放 `.env`**（已 gitignore）。脚本只读环境变量，不回显明文。
- `deploy.py` **不加 `--yes` 就是 dry-run**，只打印 payload、不发请求、不花钱。先 dry-run 核对参数。
- `deploy.py` 不做自动重试——创建实例幂等性不明，宁可人工重跑，避免一抖就建出两个计费实例。
- `power_ctl.py release` 会销毁实例和数据，必须 `--yes`；文档要求先 `power_off` 再 `release`。
- 部署前必须已抓过目标应用详情：`application_uuid` 来自 `outputs/app_details.json` 的 `uuid` 字段。
- 官方 API **没有库存/价格查询端点**，实时库存和价格去网页部署页看。

## 产物说明

| 文件 | 内容 |
|---|---|
| `outputs/tags.json` | 完整标签树 + 扁平化的 `id / name / parent / group`，按标签过滤时查 id |
| `outputs/apps.json` | 列表页字段：id、名称、作者、收藏、简介、版本列表、logo、运行/下载/使用次数 |
| `outputs/app_details.json` | 详情页字段：README 全文、author_note、tag_list、全部版本、开机命令、镜像大小 |
| `outputs/llm_raw_<标题>.txt` | 大模型返回的原文，解析失败时先看它 |
| `outputs/selection_<标题>.json` | 大模型排序 + 分数 + 理由 + 整体说明 |
| `outputs/final_choice.json` | 你最终选中的应用 ID 和名称，本 skill 的终点 |

## 评分证据：精 / 更新时间 / 下载量

| 信号 | 取字段 | 怎么用 |
|---|---|---|
| 精 / 热 | 列表和详情都有 `popular_type`；前端 `app-item.js` 把 `excellent` 显示成「精」、`hot` 显示成「热」，其余不显示 | 「精」优先于「热」优先于「无」，只在主题匹配、开箱程度接近时起决定作用。实测 243 个应用里 9 精、4 热 |
| 更新时间 | 应用级 `updated_at`（详情页 UI 显示的就是它） | >180 天直接剔除（可改 `AUTODL_ART_STALE_DAYS`）；90~180 天在理由里提示维护偏旧 |
| 下载 / 收藏 / 运行 | `download_num`、`star_num`、`runtime`、`use_num` | 同档位的排序证据，不能让热度压过主题不匹配 |

注意 `latest_version_updated_at` **不是** UI 显示的更新时间：id=112 的应用级 `updated_at` 是 2026-01-28，
而 `latest_version_updated_at` 是 2026-01-24；id=273 分别是 2026-11-23 和 2026-11-24。
脚本统一用 `updated_at`，列表没有该字段时才用版本时间推算（推算结果同样放在 `updated_at` 里发给模型，不额外标注来源）。

剔除名单会写进 `selection_<标题>.json` 的 `excluded_stale`，不会静默消失。

## 常见问题

- **「接口返回非 Success」** → 先跑 `inspect_api.py` 看是不是页面改版；不要直接改业务脚本。
- **「代理连不上」** → 到你的 `agnes-key-pool` 项目目录启动 `proxy.py`；`python check_proxy.py` 复验。
- **「解析大模型 JSON 失败」** → 看 `outputs/llm_raw_<标题>.txt`。多半是模型没按格式输出；把 `prompts/select_system.md` 第三节的格式要求再强调一次，或换 `AUTODL_ART_LLM_MODEL`。
- **候选里混进明显不相关的应用** → 改 `prompts/select_system.md` 第四节“主题匹配”，加一条针对该标题的反例；不要改排序代码。
- **详情抓取慢** → 正常。243 条全抓约 4~10 分钟，脚本每行 flush 输出进度和预计剩余时间；中断后重跑同一命令会跳过已抓过的。
- **中文乱码** → 命令前加 `PYTHONIOENCODING=utf-8`，脚本内也做了 stdout 重配。

## 边界

- 抓取阶段**没有登录代码**：不碰 Cookie/JWT、不点登录、不过验证码。这是设计前提，不是遗漏。
- 部署阶段只有**开发者 token** 调用，脚本从不知道账号密码，也不模拟登录表单。
- 花钱动作全部默认关闭：`deploy.py` 要 `--yes`，`release` 要 `--yes`。
- 更多边界与端点证据见 `../references/dev-api.md`、`../references/boundaries-next-phase.md`。
