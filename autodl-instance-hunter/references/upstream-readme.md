# 上游项目 README（脱敏存档）

> 本文是上游项目 `README.md` 的**原文存档**，只做了占位化脱敏，
> 保留当时的完整实战记录、参数说明与踩坑描述，供查证「这个结论当时是怎么来的」。
>
> **改动**：真实手机号 → `your_phone_here`；本机用户名 / 项目路径 / 解释器路径 → 占位符。
> 文中实例 ID（如 `pquxnla19v-7601110a`）是实战样本，无凭证作用，按原样保留；
> 换你自己的实例时需替换脚本里的 `TARGET_ID`。
>
> **已知与现状不符、请以代码为准的三处**：
> 1. 「环境要求：Windows / macOS / Linux」不准确——`profile_util.py` 用 Windows 专有的
>    `robocopy` 复制 Chrome profile，非 Windows 需自行改造。
> 2. `config.py` 注释「运行时需先关闭正在占用该 profile 的 Chrome 实例」是过时说明，
>    **副本方案并不需要关 Chrome**。
> 3. 文末「文件说明」只列了当时的 20 个主要文件；本 skill 实际收录 **35 个 .py**，
>    完整功能矩阵见 `scripts/README.md`。
>
> 结构化、带根因的知识请看 `zero-login-and-anti-ban.md`、`dom-and-dialogs.md`、
> `disk-expand-and-power-cycle.md`。

---

# AutoDL 自动租机机器人

基于 Playwright 的自动化脚本，用于在 [AutoDL](https://www.autodl.com/) 上自动寻找空闲机器并完成创建开机。

## 目的

- 自动登录 AutoDL。
- 在算力市场中按指定 GPU 型号、GPU 数量、计费方式遍历各地区寻找空闲机器。
- 发现空闲机器后自动进入创建页，选择基础镜像 `PyTorch` 及默认版本。
- 处理创建过程中出现的"该主机空闲 GPU 数量不足"弹窗，自动返回算力市场继续寻找。
- 全程**不调用页面刷新**，通过点击导航降低触发平台风控的概率。

## 安全警告（必读）

1. **此脚本涉及真实扣费**：点击"创建并开机"后会从你的 AutoDL 账户余额中扣费。
2. **默认开启 DRY_RUN**：首次运行请保持 `AUTODL_DRY_RUN=true`，脚本会跑到"创建并开机"前一步并停止，**不会真的下单**。
3. **人工确认闸门（防封号/防误扣费）**：即使 `DRY_RUN=false`，只要 `AUTODL_REQUIRE_HUMAN_CONFIRM=true`（默认），
   真正点击"创建并开机"前脚本会**自动截图并停在终端等你输入 `yes`**；输入其他任意内容即中止，你可自行在浏览器里操作。
   容易触发封号的危险动作因此始终由人工把关。
4. **密码不外泄**：凭证通过 `.env` 文件传入，`.env` 已加入 `.gitignore`，请勿提交到 Git。
5. **风控提示**：AutoDL 对频繁刷新/操作可能有检测，脚本已内置固定等待和重试间隔，请避免把间隔调得太小。
   轮询重试的等待时间带**随机抖动**（基准值 × 0.85~1.25 + 0~5 秒随机偏移），避免固定间隔的机器节奏被统计识别。
6. **登录交给人工**：登录是封号最高危的动作。推荐用「零登录方案」复用你真实 Chrome 的登录态，避免脚本反复触发验证码。

## 环境要求

- Python 3.8+
- Windows / macOS / Linux
- 已安装 Chromium 浏览器（Playwright 会自动下载）

## 安装

使用标准 `venv` + `pip`（不使用 uv）：

```powershell
# 进入项目目录
cd <项目目录>

# 创建虚拟环境
python -m venv venv

# 激活虚拟环境（Windows PowerShell）
venv\Scripts\Activate.ps1

# 安装依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器（仅需一次）
playwright install chromium
```

如果 `python` 命令指向的 Python 版本不合适，请使用绝对路径，例如：

```powershell
<Python解释器绝对路径> -m venv venv
```

## 配置

复制示例配置文件并填入真实值：

```powershell
copy .env.example .env
```

编辑 `.env`：

```ini
# 登录凭证
AUTODL_USERNAME=your_phone_here
AUTODL_PASSWORD=你的真实密码

# 目标机器
# GPU 型号支持**逗号分隔的优先级列表**：先找前者，全地区无货才找后者。
# 写法随意（VGPU-48g-350w / VGPU-32G 均可），脚本会归一成 AutoDL 筛选项的规范形式。
# ⚠️ 注意：vGPU-48GB-350W 与 vGPU-48GB 是 AutoDL 上两个独立型号，350W 绝不会与 48GB 混淆
#    （脚本只做整体精确匹配，不做前缀/包含匹配）。
AUTODL_GPU_MODEL=vGPU-48GB-350W,vGPU-32GB
AUTODL_GPU_COUNT=1
AUTODL_BILLING_MODE=按量计费
AUTODL_FRAMEWORK=PyTorch

# 运行行为
AUTODL_HEADLESS=false       # false=显示浏览器窗口（测试阶段建议打开）
AUTODL_DRY_RUN=true         # true=只跑到创建前，不真的下单

# 安全闸门：真正点击“创建并开机”（扣费）前必须人工确认。强烈建议保持 true
AUTODL_REQUIRE_HUMAN_CONFIRM=true

# 轮询与重试
AUTODL_RETRY_INTERVAL_SECONDS=15
AUTODL_MAX_RETRIES=0        # 0=无限循环

# 实例数量管控（出租/开机前先盘点控制台实例列表）：
# 同时运行中的实例上限（默认 2）。达上限 -> 什么都不做；
# 未达上限且有关机实例 -> 优先开机复用；无 -> 才租新机。0=不管控
AUTODL_MAX_RUNNING_INSTANCES=2
AUTODL_AUTO_POWER_ON=true

# 零登录（推荐）：本机已登录 AutoDL 的 Chrome 用户数据目录
AUTODL_USER_DATA_DIR=C:\Users\<你的用户名>\AppData\Local\Google\Chrome\User Data
# 可选：profile 副本目录（默认项目下 chrome_profile/）
# AUTODL_PROFILE_COPY_DIR=<项目目录>\chrome_profile
```

## 运行

```powershell
# 确保已激活虚拟环境
venv\Scripts\Activate.ps1

# 运行脚本
python rent_bot.py
```

## 工作流程

每轮循环**先盘点已有实例，再考虑租新机**（`manage_instances`，读 `/console/instance/list`）：

1. **运行中已达上限**（`AUTODL_MAX_RUNNING_INSTANCES`，默认 2）→ 什么都不做，直接结束。
2. **未达上限且存在"已关机"实例** → 优先点"开机"复用（某台开机失败（如该主机 GPU 资源不足）
   会自动试下一台；成功开机补足名额即结束，**无需租新**）。
   开机候选顺序：**带"资源空闲"标记**（状态旁 `.gpuTips` 渲染的提示，主机有空闲资源时才出现）
   的关机实例优先——无标记的很可能是主机资源不足、开不了机；同组内按**距释放时间最久**优先
   （最不容易中途被释放）。无标记实例排最后兜底尝试（标记缺失只是"可能"开不了，
   多试一次的成本远低于误租一台新机）。
3. **未达上限且没有已关机实例**（或全部开机失败）→ 才进入算力市场租新机。

租新机流程：

4. 设置计费方式（默认"按量计费"）、GPU 数量（默认 1）。
5. 按 `.env` 中 `AUTODL_GPU_MODEL` 的**优先级顺序**逐型号寻找（如先 `vGPU-48GB-350W`，全地区
   无货再退 `vGPU-32GB`）。每个地区先用型号复选框旁的余量 note（如 `(0/152)`）**只读预检**，
   空闲为 0 或无此型号的地区直接跳过、不产生任何点击。
6. 遍历所有地区，扫描每个地区下的机器列表；**同一地区同型号有多台可租时按
   「每GPU分配内存最大 → 数据盘可扩容最大」排序**，且跨地区也按同一规则全局择优
   （例：西北B区 内存90GB/可扩容121GB 会胜过 重庆A区 内存62GB/可扩容864GB——内存优先）。
7. 若发现"空闲"的目标机器，点击"可租"/"出租"按钮进入创建页。
8. 在创建页：
   - 点击"基础镜像" tab。
   - 打开镜像选择框。
   - 选择框架名称 `PyTorch`。
   - 框架版本、Python 版本、CUDA 版本均选择第一个可见选项。
9. 若 `DRY_RUN=false`，点击"创建并开机"。
10. 若弹出"该主机空闲 GPU 数量不足，请更换主机"，点击确定后**回到第 1 步**（先重新盘点
    已有实例，再继续寻机）。

### 只要全新机器：忽略实例库

想强制再开一台全新机器、完全不动已有实例时，在 `.env` 中设置：

```ini
AUTODL_REUSE_INSTANCES=false
AUTODL_GPU_MODEL=vGPU-48g-350w
```

- `AUTODL_REUSE_INSTANCES=false`：跳过"实例盘点/开机复用"，启动后直接去算力市场按
  型号优先级寻机（实例区只是只读记录当前 ID 供租新后对比，不做任何点击）。
- `AUTODL_GPU_MODEL` 只写一个型号（如 `vGPU-48g-350w`）就没有兜底回退，只租这一种；
  想恢复"350W 优先、32GB 兜底"就写回 `vGPU-48g-350w,vGPU-32G`。
- **运行中实例上限 `AUTODL_MAX_RUNNING_INSTANCES` 在此模式下依然生效**：运行中数量
  已达上限时照样什么都不做。想不受限就临时设为 `0`。
- 恢复正常复用流程：把 `AUTODL_REUSE_INSTANCES` 改回 `true` 即可。

## 实例元数据日志与指定关机

机器人每次**开机复用 / 租到新机 / 开机失败**都会把该实例的元数据（ID、地区、规格、
资源标记、距释放时间、时间戳）追加写入 `instances_log.json`，租到新机时通过与
本轮盘点对比自动识别新实例 ID。这样随时知道机器人动过哪些机器。

后续要关闭指定机器时：

```powershell
# 只读列出当前所有实例（ID / 状态 / 距释放时间）
python shutdown_instance.py

# 关闭指定实例（支持 ID 前缀，需输入 yes 确认；关机即停止计费，数据保留）
python shutdown_instance.py rzdjvsj47k
```

关机工具**逐个遍历行内按钮、按可见文本精确比对「关机」**，绝不会误点「设置定时关机」
（实测 `button:text-is('关机')` 因文本在子级 span 里会匹配 0 个，不能用）；
已关机实例传入会被跳过；每次关机（成功/失败）也写入 `instances_log.json`。

## 扩充数据盘

给指定实例扩容数据盘，并在扩容后自动「关机 → 立刻开机」使其生效（扩容提交后不重启不生效）：

```powershell
# 给实例数据盘扩容 +100GB（输入的是新增容量；约 ¥0.67/日，页面会回读确认）
python expand_data_disk.py pquxnla19v-7601110a 100
```

流程：更多 → 扩容数据盘 →（运行中的实例会弹"需重启生效"提示，自动点继续）→
填入新增容量 → 确定 → 自动关机并在状态变为「已关机」的**瞬间**点开机
（按量计费实例关机后保留 15 天，但 GPU 资源紧张时开机要抢，所以关→开之间零等待）。

注意：

- 扩容对话框的输入框可能长时间处于禁用状态（Element UI 初始化时序），脚本会通过 JS
  强制启用并按 Vue 的方式派发事件写入；填入后回读「扩容后容量 / 日常费用」供你核对。
- **扩容提交与重启是两步**：脚本一次跑完两步；若扩容已提交但重启失败，切勿重跑整条命令
  （会再扩一次、再扣一次费），单独做重启即可。
- 每次扩容/重启都会写入 `instances_log.json`。

## 轮询开机稀缺实例（power_on_poll.py）

目标实例已关机、但其 GPU 型号（如 vGPU-48GB-350W）极度稀缺、主机长期无空闲卡时，
反复尝试「开机」直到某轮成功：

```powershell
# 默认参数：pquxnla19v-7601110a，最多 120 轮，基准间隔 45 秒
python power_on_poll.py

# 自定义：实例ID 最大轮数 基准间隔秒
python power_on_poll.py pquxnla19v-7601110a 120 45
```

流程与防封设计：

- **零登录 + 隐身**：复用 Chrome profile 副本（零登录）、STEALTH_SCRIPT、真实 UA、
  `--disable-blink-features=AutomationControlled`、slow_mo=150。
- **只在实例列表页轮询，绝不刷新算力市场**（最高风险区）。整页导航仅开局一次，
  之后每轮**原地重新解析当前 DOM**（AutoDL 列表本身会 JS 自动刷新）。
- **随机抖动间隔**：每轮等待 = 基准 × 0.85~1.25 + 0~10 秒随机偏移，杜绝固定节奏。
- **按钮禁用/不存在 = 主机无空闲卡**：静默跳过本轮，不报错、不重试点击。
- **弹窗处理**：点「开机」后可能弹两种框——普通确认框（点确定）和「该主机GPU不足」提示框
  （唯一按钮是「我知道了」，点了仅关闭弹窗、无任何副作用；**绝不点「克隆实例」**（会新建机器扣费）
  和「无卡模式开机」（没有 GPU））。弹窗选择器必须用 `.el-message-box:visible, .el-dialog:visible`
  过滤——页面里有 15 个隐藏的 `.el-dialog`，不加 `:visible` 会让 `.first` 命中隐藏元素、
  真弹窗永远点不到、遮罩层随后拦截一切点击（2026-10-21 实测踩坑，已修复）。
  每轮开始还会先自愈清掉上一轮残留的弹窗。
- **零点击轮询**：AutoDL 只在主机**有空闲资源**时给关机实例行渲染 `.gpuTips` 资源标记
  （`parse_instance_rows` 的 `gpu_tips` 字段）。无标记时本轮**零点击**——纯 DOM 轮询，
  不给 AutoDL 发任何请求；每 `PROBE_EVERY=5` 轮做一次兜底试探点击，防止标记与实际不一致时
  永远开不了。对平台的请求量因此降到约 1/5，风控特征最小。
- 日志写入 `power_on_poll.log`（每行 flush），进程被强杀后可从日志续看进度、安全重启；
  成功/失败均写入 `instances_log.json`（event=power_on）。

## 零登录方案（强烈推荐，风控最低）

反复触发登录 / 极验滑块是封号的最高危行为。本工具支持**复用你本机已登录 AutoDL 的 Chrome 登录态**，
启动即为已登录状态，**完全不触发验证码、不触发登录风控**。

> ⚠️ 不能直接把 `AUTODL_USER_DATA_DIR` 指向 Chrome 默认 profile 路径来启动，会撞两个坑：
> 1. Chrome 正在运行会把该 profile 锁住 → 报「其他程序中的会话已打开」；
> 2. Chrome 不允许在**默认 profile 路径**上开启 Playwright 的远程调试 →
>    报 `DevTools remote debugging requires a non-default data directory`。

因此实现方式是**复制一份 profile 到非默认路径**再启动（`profile_util.py` 负责）：

1. **先在真实 Chrome 里登录 AutoDL**（手动过一次验证码即可）。登录态（JWT 令牌）会写进
   Chrome profile 的 localStorage，有效期约 60 天。
2. 在 `.env` 中设置：

   ```ini
   AUTODL_USER_DATA_DIR=C:\Users\<你的用户名>\AppData\Local\Google\Chrome\User Data
   ```

3. 正常 `python rent_bot.py`。脚本会：
   - 用 `robocopy` 把 profile 复制到 `chrome_profile/`（跳过缓存/锁定文件，约几十 MB）；
   - 用**你本机真实的 Chrome**（`channel="chrome"`）以这份副本启动；
   - 副本里带着 AutoDL 登录态 → **零登录直接进市场**，且与真实 Chrome 互不干扰、无需关闭 Chrome。

> 说明：AutoDL 的登录态在 **localStorage**（JWT，键 `token`），不在 cookie 里。副本会一并带上。
> 若令牌过期（约 60 天）需重新在真实 Chrome 登录一次，然后删除 `chrome_profile/` 让脚本重新复制。

如果不想用持久化目录，也可保留默认（普通模式）：首次登录一次后，登录态保存到 `autodl_cookies.json`，
后续自动复用（但 AutoDL 的 localStorage 过期后仍可能要求重新验证）。

## 调试与风控

- **浏览器可见**：首次测试请保持 `AUTODL_HEADLESS=false`，便于观察每一步。
- **Dry Run**：默认 `AUTODL_DRY_RUN=true`，确认流程正确后再改为 `false`。
- **重试间隔**：`AUTODL_RETRY_INTERVAL_SECONDS` 建议不小于 10 秒，避免操作过频。
- **选择器不匹配**：如果 AutoDL 前端更新导致脚本找不到元素，可打开浏览器开发者工具查看实际 DOM，修改 `rent_bot.py` 中对应的选择器列表。
- **验证码**：如果登录出现验证码，脚本会停止，需要手动处理。

## 文件说明

```text
autodl-rent-bot/
├── .env.example             # 环境变量示例
├── .gitignore               # 忽略 .env / chrome_profile / screenshots 等
├── config.py                # 读取环境变量配置
├── rent_bot.py              # 主脚本（盘点实例 + 开机复用 + 寻机 + 创建）
├── instance_log.py          # 实例元数据日志（instances_log.json）
├── shutdown_instance.py     # 按 ID 指定关机 / 只读列出实例
├── expand_data_disk.py      # 扩容数据盘 + 自动关机/开机生效
├── power_cycle_once.py      # 一次性：仅重启指定实例（扩容已提交时单独用）
├── power_on_poll.py         # 轮询开机稀缺实例（随机抖动 + 弹窗自愈 + 防封设计）
├── profile_util.py          # Chrome profile 复制（零登录核心）
├── requirements.txt         # Python 依赖
├── inspect_market.py        # 诊断：dump 市场 DOM（只读）
├── inspect_create_page.py   # 诊断：dump 创建页 DOM（只点可租，不创建）
├── inspect_instances.py     # 诊断：dump 实例列表页 DOM（只读）
├── test_instance_check.py   # 只读测试：实例解析 + 开机决策预演（不点开机）
├── test_find_machine.py     # 只读扫描测试：多型号优先级 + 择优排序（不点可租/创建）
├── test_model_priority.py   # 本地测试：型号归一化 / 优先级加载 / 排序规则（不开浏览器）
├── test_selectors.py        # 只读扫描测试（单型号，旧版，保留备用）
├── test_configure_image.py  # 镜像配置安全验证（不创建）
├── test_instance_check.py   # 只读测试：实例盘点/状态解析/决策预演（不点开机/创建）
├── test_skip_reuse.py       # 只读测试：REUSE_INSTANCES=false 跳过复用、直接租新（不点任何按钮）
└── README.md                # 本文件
```

## 已知限制

- 镜像版本选择依赖页面级联菜单的顺序，如果 AutoDL 调整默认排序，可能需要手动修改脚本。
- 地区列表和机器卡片的 DOM 选择器基于当前页面结构，若页面改版需要同步更新。
- 登录阶段的验证码、二次验证未处理。

## 免责声明

本脚本仅用于学习和自动化 convenience，使用者需自行承担因自动下单产生的费用与平台规则风险。
