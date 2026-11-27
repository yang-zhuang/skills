---
name: autodl-art-rent-bot
description: 在 AutoDL Art 应用广场做「零登录抓取 → 大模型按预设标题择优 → 人工终选 → 开发者 token 选卡部署」的完整流程。匿名调用应用列表、详情、标签树、周榜接口抓公开资料，用本地 agnes-key-pool 代理让大模型给出 Top5 排序，再由人决定选哪个；选定后可用官方开发者 API（token 鉴权）查看实例、选 GPU 规格、创建实例、开关机与释放。This skill should be used when 用户要求在 autodl.art/app/market 上找应用、按“视频生成/图像生成/TTS”等预设标题筛选应用、让大模型对应用排序、或在 AutoDL Art 上租实例选卡部署。关键词：AutoDL Art、autodl.art、应用广场、应用市场、零登录抓取、大模型择优、Top5、预设标题、视频生成、开发者 token、选卡、部署、创建实例。边界：抓取阶段零登录；部署阶段用开发者 token，脚本绝不碰登录表单，创建实例默认 dry-run，必须 --yes 才花钱。
agent_created: true
---

# AutoDL Art 应用广场：零登录抓取与大模型择优

## 和 `autodl-rent-bot` 的区别（先分清楚）

| 维度 | `autodl-rent-bot` | 本 skill `autodl-art-rent-bot` |
|---|---|---|
| 站点 | `autodl.com` 算力市场 / 实例列表 | `autodl.art/app/market` 应用广场 |
| 目标 | 把稀缺 GPU 实例抢到手并管控 | 先选哪个应用，再用官方 API 选卡部署 |
| 登录态 | 必须复用已登录 Chrome profile 的 JWT | 抓取零登录；部署用**开发者 token**（脚本不碰登录表单） |
| 抓取方式 | Playwright 读 DOM | 匿名公开 JSON 接口 + 官方 dev API |
| 计费动作 | 开机/扩容/下单（有人工闸门） | `deploy.py` 默认 dry-run，加 `--yes` 才创建 |
| 最终决定 | 人工确认下单 | 人工从 Top N 里选，再人工确认参数 |

两者是上下游关系：本 skill 输出 `final_choice.json`（选中的应用），再用 `deploy.py` 创建实例；机器到手之后的 SSH 操作用 `autodl-remote-shell`。

---

## 核心事实（2026-11-24 实测）

应用广场是 Vue SPA，但数据走公开接口，**匿名直接可访问**，无需浏览器登录态、无需 cookie：

| 用途 | 方法与路径 | 请求体 | 实测结果 |
|---|---|---|---|
| 应用列表 | `POST /api/v1/application` | `{"page_index":1,"page_size":100,"name":"","unified_tag_ids":[]}` | 200 `Success`，result_total=243，max_page=3 |
| 应用详情 | `POST /api/v1/application/square/detail` | `{"application_id":273}` | 200 `Success`，含 readme、author_note、tag_list、8 个版本 |
| 标签树 | `GET /api/v1/unified_tag/tree` | 无 | 200 `Success`，3 个一级分类、66 个末级标签 |
| 周榜 | `POST /api/v1/application/runtime/week` | `{}` | 200 `Success`，10 条 |

接口路径来自前端 JS：`assets/deploy.3afb0f48.js` 定义接口，`assets/index.a5d27239.js` 组装列表请求体，
`assets/index.cf7abf41.js` 是入口。页面改版后先跑 `inspect_api.py` 重新验证，不要凭记忆改脚本。

---

## 何时使用 / 何时不使用

**使用：**
- 要在 art 应用广场按默认顺序抓取每个应用的公开信息；
- 有预设标题（如“视频生成”），想让大模型按匹配度给出 Top5，并说明每条的理由和短板；
- 想人工决定最终选哪个，而不是让脚本自动下单；
- 页面/接口改版后，需要零登录地重新核对字段结构。

**不使用：**
- 要抢 autodl.com 的稀缺卡 → 用 `autodl-rent-bot`；
- 机器已到手、要 SSH 装环境 → 用 `autodl-remote-shell`；
- 想让脚本自己输账号密码 / 过验证码换登录态 → **不做**，登录由人完成，脚本只带 token。

---

## 决策树

```
想做什么？
├─ 先看看接口还通不通 / 字段变了没
│   └─ python inspect_api.py
├─ 抓应用广场
│   ├─ 全部（默认排序）→ python fetch_apps.py
│   ├─ 只看某个标签 → 先看 outputs/tags.json 找 id，再 python fetch_apps.py 100 <tag_id>
│   └─ 抓每个应用的详情 → python fetch_details.py 30（或 243 / 0 表示全部，可断点续抓）
├─ 让大模型按预设标题排序
│   ├─ 先验证本地代理 → python check_proxy.py
│   └─ python select_apps.py "视频生成" 5
├─ 人工终选
│   └─ python choose_final.py "视频生成"
├─ 用开发者 token 看/管实例（需要 .env 里的 AUTODL_DEV_TOKEN）
│   ├─ python dev_check.py                 验证 token、列实例
│   ├─ python select_gpu.py                对齐规格 ID 与网页显示名
│   ├─ python deploy.py --app-id 273 --gpu 5090-p --gpu-amount 1          dry-run，不发请求
│   └─ python deploy.py --app-id 273 --gpu 5090-p --gpu-amount 1 --yes    真正创建（花钱）
├─ 抢机器（推荐 --signal 信号模式：有货才点，对齐 autodl 的 .gpuTips）
│   ├─ python extract_jwt.py --login                                   一次性：登录 art 一次，JWT 自动写入 .env
│   ├─ python grab.py stock 西北B区,北京B区                             看库存行情（只读，需网页 JWT）
│   ├─ python grab.py list                                             先看实例和状态
│   ├─ python grab.py power <uuid> --interval 60 --hours 12 --yes         抢开机（已关机实例）
│   └─ python grab.py create --app-id 273 --gpu 5090-p,pro6000-p --region 西北B区,北京B区 --signal --hours 12 --yes
│                                                                      抢创建（有货才点；不加 --signal 则盲试）
└─ 实例开关机
    ├─ python power_ctl.py list / status <id> / on <id> / off <id>
    └─ python power_ctl.py release <id> --yes   （销毁，不可逆）
```

---

## 不可违背的硬规则

1. **抓取不登录，部署用 token**：抓应用资料时不要复用 Cookie/JWT、不要点登录、不要触发验证码；部署/抢机阶段只读 `.env` 里的 `AUTODL_DEV_TOKEN`（用户在控制台手动生成），**绝不把账号密码/验证码交给脚本，脚本全程不碰登录表单**。
2. **防风控是硬编码**：grab.py 永远单线程串行、请求间隔强制 ≥1 秒、轮询间隔 ≥30 秒带抖动、连续失败 ≥15 次自动退避（最多 4 倍）、致命错误（token 无效/参数错/余额不足）立刻熔断退出。这些规则不可通过参数关闭。
3. **花钱动作默认不动**：`deploy.py` 和 `grab.py create` 不加 `--yes` 只 dry-run；`power_ctl.py release` 不加 `--yes` 拒绝执行。创建/删除前回显完整参数供人工核对。
4. **限速**：请求间隔 1.0~2.5 秒随机抖动；失败最多重试 2 次。243 个详情全抓约 4~10 分钟，**逐行输出进度**，不要关掉终端等结果。
5. **人工终选是硬闸门**：大模型只输出排序和理由，脚本绝不自动替用户定应用。
6. **证据落地**：每次抓取都写 `outputs/*.json`；模型原文单独存 `llm_raw_<标题>.txt`，解析失败时能直接看原文，不许靠猜。
7. **评分证据必须显式**：平台标记「精」=`popular_type=="excellent"`、「热」=`hot`；更新时间取应用级 `updated_at`（**不是** `latest_version_updated_at`，后者与 UI 显示不一致）。超过 `STALE_DAYS`（默认 180 天）的应用在进大模型前被确定性剔除，90~180 天要在理由里提示维护偏旧。下载量、收藏数、运行时间只作同档位排序依据，不能压倒主题匹配。
8. **产物与凭据不入库**：`outputs/` 全量忽略；`.env`（含 token）、`keys.json`、截图、日志一律不进 Git。
9. **提示词可改但逻辑不改**：挑选标准全文在 `scripts/prompts/select_system.md`（Markdown，自包含，含硬边界/字段表/正反例/自检清单），改需求只改它；陈旧闸门的天数在 `config.py`。

---

## 快速开始

```bash
cd <本 skill 目录>/scripts
# 无第三方依赖；Python 3.10+。大模型走本机 agnes-key-pool 代理（127.0.0.1:8765）
python inspect_api.py                 # 1. 接口与字段体检（只读）
python check_proxy.py                 # 2. 代理与模型连通性
python fetch_apps.py                  # 3. 抓 243 个应用 + 标签树
python fetch_details.py 30            # 4. 抓前 30 个详情；想全抓用 243 或 0
python select_apps.py "视频生成" 5    # 5. 大模型择优（本地代理）
python choose_final.py "视频生成"     # 6. 人工终选，写入 final_choice.json

# 7. 部署（需要包根 .env 里的 AUTODL_DEV_TOKEN）
python dev_check.py                   # 7a. 验证 token、列实例（只读）
python select_gpu.py                  # 7b. 看规格 ID 对照
python deploy.py --app-id 273 --gpu 5090-p --gpu-amount 1 --dry-run   # 7c. 只打印，不花钱
python deploy.py --app-id 273 --gpu 5090-p --gpu-amount 1 --yes       # 7d. 真创建
python power_ctl.py list              # 7e. 看实例状态
```

`scripts/run_pipeline.sh` 是上述命令的用法备忘，不是自动执行脚本。

---

## 资源索引

- `scripts/README.md` — 每个脚本的用法、参数、产物和故障排查。
- `scripts/prompts/select_system.md` — system prompt 全文（Markdown），挑选标准都在这里。
- `scripts/prompt.py` — 加载 prompt 并把应用字段压成摘要的构造函数。
- `references/anonymous-api.md` — 接口的发现过程、请求/响应结构、字段字典、限速与改版排查。
- `references/llm-prompt-design.md` — 为什么这样设计提示词、如何按新标题改、正反例。
- `references/dev-api.md` — 开发者 API 的鉴权、端点、规格 ID 对照、以及「脚本不碰登录」的边界。
- `references/boundaries-next-phase.md` — GPU 选择与部署的演进记录与安全边界。

> 代码注释与 docstring 是第一手说明；`references/` 负责讲「为什么」和「什么绝对不能做」。
