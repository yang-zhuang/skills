---
name: autodl-rent-bot
description: 在 AutoDL 上「把稀缺 GPU 实例弄到手并管好」的完整作业方法——零登录复用 Chrome 登录态、防封号的随机抖动与零点击轮询、实例盘点与开机复用、按型号优先级寻机、数据盘扩容与关机/开机生效、按 ID 指定关机，以及页面改版后的只读 DOM 回归诊断。核心是把「容易触发封号、容易误扣费」的动作交给人工闸门，把长时间等待做成对平台近乎零请求的低风险轮询。This skill should be used when 用户要求抢/租稀缺 GPU 机型（如 vGPU-48GB-350W）、轮询把已关机实例开机、查看或管理 AutoDL 容器实例、扩充数据盘、指定实例关机、排查「该主机GPU不足」弹窗、或 AutoDL 页面改版后需要重新核对选择器时。关键词：AutoDL、租机、抢卡、开机轮询、资源空闲标记、gpuTips、零登录、防封号、数据盘扩容、克隆实例、无卡模式开机。注意：机器已到手后要 SSH 进去拉代码/下模型/跑服务，请用同仓库的 autodl-remote-shell skill，两者是上下游关系。
agent_created: true
---

# AutoDL 稀缺实例获取与管控

## Overview

这套东西解决的是 **AutoDL 上「机器还没到手」到「机器稳定在用」之间** 的全部问题：
在账号安全优先的前提下，把一台长期显示「该主机 GPU 不足」的稀缺实例开机，
并管好它的开机、扩容、关机与元数据。

它来自一次真实实战：目标机型 `vGPU-48GB-350W` 几乎不释放，连续轮询 **9.75 小时 / 500+ 轮**
（2026-10-21 16:28 → 10-22 02:14）后，主机终于腾出空闲卡，30 秒内完成开机，
期间账号无任何风控异常。整个设计围绕一条用户硬约束：

> **容易触发封号、容易误扣费的动作必须留给人工；机器人只做等待、只读观察和已获授权的低风险点击。**

`scripts/` 里是当时实际跑通的 **35 个 `.py` 整组**（上游 3836 行，本仓库 3866 行——
多出的 30 行是把本机绝对路径改成可移植写法的注释），保持原有分工与互相 import 关系，
不是精简版示例。`references/` 里是「为什么这么设计」的根因与踩坑明细。

---

## 何时使用（触发侧）

- 目标 GPU 机型长期无货 / 「一直没有处于释放状态」，需要长时间蹲守开机；
- 已租到的实例被关机，想复用而不是重新租一台；
- 需要从算力市场按型号优先级找空闲机器（含多型号兜底、按内存/可扩容排序）；
- 要给实例扩数据盘，或扩容已提交、只差重启生效；
- 要按实例 ID 指定关机 / 只读盘点当前所有实例；
- AutoDL 前端改版，选择器失效，需要零费用地重新摸 DOM；
- 看到「该主机空闲 GPU 不足」弹窗，想知道该点什么、不该点什么。

## 何时**不**使用（边界侧）

- **机器已经在运行中，要 SSH 进去装环境、拉 GitHub 代码、下模型权重、启推理服务、开 6006 端口**
  → 用同仓库的 `autodl-remote-shell` skill。本 skill 到「实例运行中」为止，不碰实例内部。
- 只想本地跑个不联网的单元测试 → 直接看 `scripts/test_model_priority.py`，不必加载整套流程。
- 需要无人值守地自动下单新机器并跳过人工确认 → **不做**。这是本项目明确拒绝的场景。

---

## 决策树

```
目标是什么？
├─ 要一台全新机器（算力市场寻机）
│   └─ rent_bot.py（DRY_RUN=true 先空跑验证 → 人工确认后才允许真实下单）
│       └─ 只要某一型号、不动已有实例：AUTODL_REUSE_INSTANCES=false
├─ 已有「已关机」实例，想开回来
│   ├─ 主机长期无空闲卡（稀缺机型）→ power_on_poll.py <实例ID> <轮数> <基准间隔秒>
│   └─ 普通情况 → rent_bot.py 的 manage_instances 自动开机复用
├─ 扩数据盘
│   ├─ 扩容还没提交 → expand_data_disk.py <实例ID> <新增GB>（含自动关机→立刻开机）
│   └─ 扩容已提交、只差重启 → power_cycle_once.py（绝不重跑上一条，会二次扣费）
├─ 关掉指定实例 → shutdown_instance.py [实例ID前缀]（不带参数 = 只读列出）
├─ 改 .env 的某个模式 → set_fresh_rent_env.py / set_gpu_model_env.py / set_instance_env.py
└─ 页面改版 / 选择器失效 / 环境异常
    ├─ 只读 DOM 探查 → inspect_*.py / analyze_*.py / extract_*.py
    ├─ 零费用回归测试 → test_*.py / diagnose_filter.py
    └─ 浏览器与登录态诊断 → verify_env.py / verify_token.py / check_*_*.py
```

---

## 不可违背的硬规则

### A. 账号安全（最高优先级）

1. **零登录**：必须用 `profile_util.ensure_profile_copy()` 把已登录的 Chrome profile
   **复制到非默认路径**再启动。直接拿 Chrome 默认 profile 路径启动会同时踩两个坑：
   运行中 profile 被锁、Chrome 拒绝在默认 profile 上开远程调试
   （`DevTools remote debugging requires a non-default data directory`）。
   登录态是 **localStorage 里的 JWT（键 `token`，约 60 天有效）**，不在 cookie 里。
   登录/验证码是封号最高危动作，绝不交给脚本反复触发。
2. **不刷算力市场**：长时间蹲守只在 `/console/instance/list` 自己的实例页进行；
   整页导航开局一次，之后原地重新解析 DOM（列表本身会 JS 自动刷新）。
3. **随机抖动**：轮间等待 = 基准 × 0.85~1.25 + 0~10 秒随机。基准间隔**不要低于 45 秒**，
   固定间隔是标准的机器节奏，容易被风控统计识别。
4. **零点击轮询**：`.gpuTips` 资源标记是只读信号（主机有空闲卡才渲染）。
   无标记时对平台**零请求**，只在本地读 DOM；每 `PROBE_EVERY=5` 轮做一次兜底试探点击。
   实测 4.5 小时内对平台真实请求不到 50 次。
5. 失败不崩溃、每行 flush 写日志：进程被强杀也能从日志续看进度、安全重启（轮询是无状态的）。

### B. 费用安全（不可自动点的按钮）

6. **绝不点「克隆实例」**：会新建一台计费实例、ID 不同、产生额外费用。
   **绝不点「无卡模式开机」**：没有 GPU，跑不了模型。
   **绝不点「创建并开机」** 除非 `DRY_RUN=false` 且 `REQUIRE_HUMAN_CONFIRM` 的人工闸门输入了 `yes`。
7. ⚠️ **`power_on_poll.py` 会自动点开机计费确认框**：其 `SAFE_BUTTONS` 白名单包含
   「确定 / 确认 / 立即开机」。实战那次点的就是「确认开机吗？实例将按 ¥1.78/时整点扣费」→「确定」。
   **这只在用户已明确授权「把这台机器开机」时才允许**。把本 skill 复用到别的实例前，
   必须确认这次开机是用户要的；若要改成通用无人值守，先给计费确认框加人工闸门，不要默认放行。
8. 弹窗一律用 `.el-message-box:visible, .el-dialog:visible` 过滤。实例页 DOM 里有
   **15 个隐藏的 `.el-dialog`**，不加 `:visible` 会让 `.first` 命中隐藏元素 →
   真弹窗点不到 → 遮罩层拦截后续所有点击（实测卡死过一次）。每轮开始先自愈清残留弹窗。
9. 按钮匹配必须**遍历行内 button、按 `inner_text().strip()` 精确比对**。
   `button:text-is('关机')` 实测匹配 0 个，因为文本在子级 `span` 里。
10. **扩容提交与重启是两步**：扩容已提交但重启失败时，只跑 `power_cycle_once.py`，
    **切勿重跑 `expand_data_disk.py`**（会再扩一次、再扣一次费）。
11. **关机→开机之间零等待**：稀缺机型关机后 GPU 随时被别人抢走，
    `wait_shutdown_then_power_on` 在状态变成「已关机」的瞬间就点「开机」。

### C. 工程约束

12. **35 个 `.py` 必须同一目录平铺**，不能按功能分子目录：其中 20 个显式 import 本地模块
    （大多指向 `rent_bot.py`），移动就会全线 `ModuleNotFoundError`。
13. **绝不提交**：`.env`（含真实账号密码）、`chrome_profile/`（含 JWT 登录态）、
    `screenshots/`、`*.log`、`instances_log.json`、`*_full.html`（DOM 快照可能含实例 ID）。
    `.gitignore` 已覆盖；改代码后推送前再扫一遍。
14. `profile_util.py` 用 Windows 的 `robocopy` 复制 profile，**非 Windows 需自行改造**这一处；
    上游 README 写「支持 macOS/Linux」是不准确的。
15. 页面改版后**先用只读诊断脚本**（`inspect_*` / `test_*`）核对 DOM，再动核心脚本；
    绝不拿真实下单流程当调试手段。

---

## 复用前必读：这套代码该怎么吸收

按「不机械复制粘贴」的原则，先看懂再改：

| 维度 | 内容 |
|---|---|
| **全盘吸收** | 随机抖动 + `.gpuTips` 零点击轮询 + 弹窗按钮白名单 + 每行 flush 的自恢复日志；这四件是 9.75 小时无风控异常的关键。 |
| **看业务调整** | 实例 ID、目标 GPU 型号优先级、轮数/基准间隔、Chrome profile 路径、运行中实例上限。全部在 `.env` 或脚本顶部常量。 |
| **该抛弃** | 账号密码（只能在 `.env`）、一次性 DOM 快照与日志、`AUTODL_REUSE_INSTANCES=false` 这种为单次「只想租新机」而设的临时模式。 |
| **埋的雷** | 「克隆实例 / 无卡模式开机」结构上绝不可点；计费确认框默认会被自动点（见硬规则 7）；`config.py` 里「运行时需先关闭 Chrome」是过时注释，副本方案并不需要关 Chrome；8 个诊断脚本的本机绝对路径已改为按当前用户 / 脚本目录推导（清单见 `scripts/README.md`），但业务逻辑与选择器仍是 2026-10 的页面结构，改版需重查。 |

---

## 资源索引

- `scripts/README.md` — **先读这个**：35 个脚本的功能矩阵（用途 / 何时用 / 参数 / 费用风险等级）、
  安装运行、换机器适配清单、与上游项目的差异。
- `scripts/.env.example` — 脱敏配置模板；`scripts/requirements.txt` — 依赖（playwright、python-dotenv）。
- `references/zero-login-and-anti-ban.md` — 零登录原理、JWT 位置、防封号参数与实测数据、红线。
- `references/dom-and-dialogs.md` — Element UI 选择器坑、两类弹窗的区分与处理、`.gpuTips` 语义、
  按钮精确匹配、镜像四级级联。
- `references/disk-expand-and-power-cycle.md` — 扩容输入框 disabled 的 JS 强制写入、Vue 事件派发、
  关开机生效、二次扣费陷阱。
- `references/upstream-readme.md` — 上游项目 README 的脱敏存档，保留原始实战记录与完整参数说明。

> 代码里的注释与 docstring 是第一手说明；本 skill 的文档负责讲「为什么」和「什么绝对不能做」。
> 两者冲突时，**以代码为准**，并把差异补进文档。
