# scripts/ — 35 个脚本功能矩阵与使用说明

## 目的

`scripts/` 放的是 2026-10 那次实战**实际跑通的整套代码**（35 个 `.py`），
上游原始版本共 3836 行，本仓库收录版 **3866 行**（多出的 30 行是把本机绝对路径
改成可移植写法时新增的注释，没有任何业务逻辑改动）。它们是一个**互相 import 的耦合包**：其中 20 个显式依赖本地模块
（大多指向 `rent_bot.py`）。因此：

> **35 个 `.py` 必须留在同一目录平铺，不能按功能分子目录**，否则
> `from rent_bot import ...` 全线 `ModuleNotFoundError`。

先读 `../SKILL.md` 的硬规则，再按下面的矩阵挑脚本；根因和踩坑明细在 `../references/`。

---

## 费用风险等级（先看这个）

| 标记 | 含义 |
|---|---|
| 🔴 高 | 可能产生**新的持续计费**：真实下单、扩容（约 ¥0.67/日/100GB）、自动确认开机计费框 |
| 🟡 中 | 改变**已有实例**状态：开机 / 关机 / 重启，或需要终端 `yes` 确认 |
| 🟢 只读 | 不点任何计费按钮，零费用（DOM 探查、只读回归、本地单元测试） |
| ⚪ 库 | 被其它脚本 import，自己不执行动作 |

**默认安全设置**：`.env` 里 `AUTODL_DRY_RUN=true`、`AUTODL_REQUIRE_HUMAN_CONFIRM=true`、
`AUTODL_HEADLESS=false`。**不要一上来就改它们。**

⚠️ 特别注意：`power_on_poll.py` 的弹窗白名单含「确定 / 确认 / 立即开机」，
**会自动点「确认开机吗？实例将按 ¥1.78/时整点扣费」的计费确认框**。
只在用户明确授权「把这台机器开机」时运行。

---

## 一、核心运行时（8 个 / 2206 行）

| 脚本 | 行 | 作用 | 用法（位置参数，无 `--` 选项） | 风险 |
|---|---:|---|---|---|
| `rent_bot.py` | 1092 | 主流程：盘点已有实例 → 开机复用 → 按型号优先级寻机 → 配置镜像 → 创建 | `python rent_bot.py` | 🔴 |
| `config.py` | 123 | 读 `.env`、型号归一化 `normalize_model`、所有开关与校验 | 被 import | ⚪ |
| `profile_util.py` | 121 | 用 `robocopy` 复制 Chrome profile 到非默认路径（零登录核心） | 被 import；`python profile_util.py` 可强制重拷 | ⚪ |
| `instance_log.py` | 27 | 把实例元数据追加写入 `instances_log.json` | 被 import | ⚪ |
| `power_on_poll.py` | 321 | **轮询开机稀缺实例**：随机抖动 + `.gpuTips` 零点击 + 弹窗自愈 + 每行 flush 日志 | `python power_on_poll.py [实例ID] [最大轮数] [基准间隔秒]` | 🟡/🔴 |
| `shutdown_instance.py` | 192 | 不带参数 = **只读列出**所有实例；带 ID（支持前缀）= 指定关机 | `python shutdown_instance.py` / `python shutdown_instance.py <ID前缀>` | 🟢/🟡 |
| `expand_data_disk.py` | 261 | 扩容数据盘 + 自动「关机→立刻开机」使扩容生效 | `python expand_data_disk.py <实例ID> <新增GB>` | 🔴 |
| `power_cycle_once.py` | 69 | **仅**重启指定实例（扩容已提交、重启失败时补跑，避免二次扣费） | `python power_cycle_once.py`（`TARGET_ID` 在脚本顶部） | 🟡 |

`power_on_poll.py` 的参数说明（三者都可省略，用脚本顶部默认值）：

- `实例ID`：目标实例，默认 `TARGET_ID`；先用 `python shutdown_instance.py` 只读列出拿到真实 ID。
- `最大轮数`：跑满即停。实战用过 `120`（约 1.5~2 小时）、`240`（约 4.5 小时）、
  `700`（约 13.5 小时，通宵）。轮询**无状态**，随时停、随时重跑不丢进度。
- `基准间隔秒`：建议 **60**（不低于 45）。实际等待 = 基准 × 0.85~1.25 + 0~10 秒随机抖动。

例：`python power_on_poll.py <实例ID> 700 60`

---

## 二、模式切换（3 个 / 98 行）

只改当前目录的 `.env`，**只回显被改的键、不回显其它敏感行**（这是刻意的：脚本本身也是用法备忘）。

| 脚本 | 行 | 作用 | 用法 | 风险 |
|---|---:|---|---|---|
| `set_fresh_rent_env.py` | 41 | `AUTODL_REUSE_INSTANCES=false` + 只租 `vGPU-48g-350w`（忽略实例库，强制租全新机） | `python set_fresh_rent_env.py` | ⚪ |
| `set_gpu_model_env.py` | 30 | 把 `AUTODL_GPU_MODEL` 改成多型号优先级列表（350W 优先、32GB 兜底） | `python set_gpu_model_env.py` | ⚪ |
| `set_instance_env.py` | 27 | 追加 / 更新实例管控配置（`MAX_RUNNING_INSTANCES` 等） | `python set_instance_env.py` | ⚪ |

> 本 skill 里 `set_fresh_rent_env.py` 已把上游硬编码的本机绝对路径
> 改为 `Path(__file__).with_name(".env")`，与同组另两个脚本一致，换目录也能正确改到当前 `.env`。

---

## 三、只读安全验证（7 个 / 534 行）

**页面改版后先跑这一组**，零费用地确认解析与决策仍正确，再动核心脚本。

| 脚本 | 行 | 验证什么 | 风险 |
|---|---:|---|---|
| `test_model_priority.py` | 53 | 型号归一化 / 优先级列表 / 排序规则，**不开浏览器**，最快的第一道检查 | 🟢 |
| `test_selectors.py` | 77 | 新选择器在真实市场页能否正常工作（单型号旧版，保留备用） | 🟢 |
| `test_find_machine.py` | 76 | 多型号优先级 + 择优排序全流程，不点「可租 / 创建」 | 🟢 |
| `test_instance_check.py` | 97 | 实例列表解析 + 开机决策预演，绝不点任何按钮 | 🟢 |
| `test_skip_reuse.py` | 45 | `REUSE_INSTANCES=false` 时 `manage_instances` 应跳过开机、直接返回 `need_rent` | 🟢 |
| `test_configure_image.py` | 95 | 只点一次「可租」进创建页、配置 PyTorch 镜像后关闭，不创建 | 🟢 |
| `diagnose_filter.py` | 91 | 型号筛选在「勾选后 / 切换地区后」是否仍生效（只读） | 🟢 |

---

## 四、DOM 探查与环境诊断（17 个 / 1028 行）

前 4 个是**离线分析**：读已经导出的 `*_full.html` / json 快照，不联网。
中间是**在线只读 dump**：打开页面、截图、导出结构，绝不点计费按钮。
最后是**环境/登录态诊断**。

| 脚本 | 行 | 作用 | 风险 |
|---|---:|---|---|
| `analyze_gputips.py` | 27 | 分析 `instance_full.html` 里 `.gpuTips` 容器与已关机行的标记结构 | 🟢 |
| `analyze_instance_html.py` | 30 | 分析 `instance_full.html`：真实实例行的精确结构与状态/按钮位置 | 🟢 |
| `extract_selectors.py` | 25 | 从导出的 HTML 抽取候选选择器 | 🟢 |
| `extract_create.py` | 36 | 从创建页 HTML 抽取镜像级联等结构 | 🟢 |
| `inspect_market.py` | 188 | 进算力市场 dump 关键 DOM（含零登录分支）；不点「可租」 | 🟢 |
| `inspect_create_page.py` | 91 | 只点一次「可租」打开配置页 dump 结构后关闭；**绝不点创建并开机** | 🟢 |
| `inspect_instances.py` | 96 | dump 实例列表页 DOM、截图、抽取状态/按钮/规格 | 🟢 |
| `inspect_buttons.py` | 77 | dump 运行中实例行内所有按钮的类名 / 文本 / 可见性 | 🟢 |
| `inspect_expand.py` | 82 | 只读探查「更多」下拉 + 「扩容数据盘」弹窗 DOM，不点确定 | 🟢 |
| `inspect_expand2.py` | 81 | 点开扩容弹窗后 dump 所有弹层元素，不点确定 | 🟢 |
| `verify_env.py` | 30 | **不访问 AutoDL**，只验证 Playwright 能否启动浏览器 | 🟢 |
| `verify_token.py` | 58 | 从 leveldb 抓 `token` 后的 JWT、解析 payload，确认副本带有效登录态 | 🟢 |
| `check_profile.py` | 34 | 检查 Chrome profile 目录结构 | 🟢 |
| `check_cookies.py` | 34 | 读 Cookies SQLite，确认 cookie 里没有登录令牌（登录态在 localStorage） | 🟢 |
| `check_localstorage.py` | 51 | 扫描 leveldb 里 `token` / `Authorization` 等键附近的可读字符串 | 🟢 |
| `dump_realtime.py` | 44 | dump 真实 Chrome 的 leveldb 内容 | 🟢 |
| `scan_all_profiles.py` | 44 | 扫描所有 Chrome profile，找带 `_l_xxxxx` token 的文件 | 🟢 |

---

## 五、安装与首跑顺序

用**标准 `venv` + `pip`**（本项目不使用 uv），Python 3.10+（代码用了 `str | None` 类型写法）：

```bash
cd <本 skill 目录>/scripts
python -m venv venv
venv\Scripts\Activate.ps1          # Windows PowerShell；Linux/macOS 用 source venv/bin/activate
pip install -r requirements.txt    # playwright>=1.40.0, python-dotenv>=1.0.0
playwright install chromium        # 零登录方案用本机真实 Chrome，可跳过；普通模式需要
copy .env.example .env             # 然后填账号、AUTODL_USER_DATA_DIR
```

首跑顺序（**每一步都验证过再进下一步**）：

```bash
python verify_env.py               # 1. 浏览器能不能起
python verify_token.py             # 2. profile 副本里有没有有效 JWT
python inspect_instances.py        # 3. 实例页 DOM 结构是否和脚本假设一致
python test_model_priority.py      # 4. 型号归一化/排序（不开浏览器）
python test_instance_check.py      # 5. 解析与决策预演（只读）
python shutdown_instance.py        # 6. 只读列出实例，拿到真实 ID / 状态 / 距释放时间
python rent_bot.py                 # 7. DRY_RUN=true 空跑一遍寻机流程
```

只有确认 7 全程符合预期，才考虑把 `AUTODL_DRY_RUN` 改成 `false`；
且 `AUTODL_REQUIRE_HUMAN_CONFIRM=true` 会在此之前截图并等终端输入 `yes`。

---

## 六、换机器 / 换实例必须改的清单

整套代码是从一次实战里长出来的，本机绝对路径已参数化，但**下面这些仍需按你的环境逐项确认**：

| 类别 | 位置 | 改成什么 |
|---|---|---|
| 账号 | `.env` 的 `AUTODL_USERNAME` / `AUTODL_PASSWORD` | 你自己的；**`.env` 绝不提交** |
| Chrome profile 路径 | `.env` 的 `AUTODL_USER_DATA_DIR` | 本机真实 Chrome 的 `User Data` 目录 |
| 诊断脚本里的 Chrome profile 路径 | `check_profile.py`、`check_cookies.py`、`check_localstorage.py`、`dump_realtime.py`、`scan_all_profiles.py`、`verify_token.py`、`extract_create.py`、`extract_selectors.py` | **已改为按当前用户 / 脚本目录自动推导**，一般无需再改；若 Chrome 用户数据目录不在标准位置，只需改这几处推导式 |
| 实例 ID | `power_on_poll.py` / `power_cycle_once.py` 的 `TARGET_ID`；`inspect_buttons.py` / `inspect_expand.py` / `inspect_expand2.py` 的 `TARGET_ID`；`expand_data_disk.py` / `power_on_poll.py` 文档字符串示例 | 换成 `python shutdown_instance.py` 列出的真实 ID |
| 型号优先级 | `.env` 的 `AUTODL_GPU_MODEL` | 按你要抢的机型；注意 `vGPU-48GB-350W` ≠ `vGPU-48GB` |
| 非 Windows | `profile_util.py` 的 `robocopy` | 换成 `shutil.copytree` + 忽略列表，并自行处理 `Singleton*` 锁文件 |

**改动范围（只动路径解析，没有动业务逻辑与选择器）**：
9 个脚本里的本机绝对路径改成按 `Path(__file__).parent` / `os.path.expanduser("~")` 推导，
`config.py` 注释里的示例路径换成占位符——目的是公开仓库不留本机用户名与绝对路径，
同时让脚本在任意 clone 目录都能跑。核心 8 个运行时脚本的**业务流程、选择器、
防封号参数、弹窗白名单一律保持上游原样**。

---

## 七、与上游项目的差异（透明记录）

| 项 | 说明 |
|---|---|
| 35 个 `.py` | 全部平铺收录，无删减。**只改路径解析，未改业务逻辑 / 选择器 / 防封号参数**：① `set_fresh_rent_env.py` 的 `.env` 路径 → `Path(__file__).with_name(".env")`（同组另两个脚本本来就是这么写的）；② 8 个诊断脚本（`check_profile` / `check_cookies` / `check_localstorage` / `dump_realtime` / `scan_all_profiles` / `verify_token` / `extract_create` / `extract_selectors`）的本机绝对路径 → 按当前用户 / 脚本目录推导；③ `config.py` 注释示例路径 → 占位符 |
| `.env` | **不收录**（含真实账号密码） |
| `.env.example` | 收录，但真实手机号 → `your_phone_here`，本机路径 → 占位符 |
| `.gitignore` | 收录并加强：额外排除 `*.log`、`screenshots/`、`instances_log.json`、`*_full.html` 等运行时产物 |
| `README.md` | 上游原文经脱敏后存到 `../references/upstream-readme.md`；本文件是新写的功能矩阵 |
| 运行时产物 | `chrome_profile/`、`screenshots/`、`*.log`、`*.html` 快照、`instances_log.json`、`__pycache__/` **一律不收录** |

---

## 八、免责声明

这些脚本会登录**真实账号**、点击**真实计费页面**。使用者需自行承担自动下单产生的费用、
平台规则风险，以及页面改版导致选择器失效的后果。
本项目把「容易触发封号、容易误扣费」的动作交给人工闸门，正是为了把风险降到最低；
**但闸门的最后一道永远是人**。
