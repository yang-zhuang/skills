---
name: autodl-community-image-analysis
description: 对 AutoDL 社区镜像（应用名如「MINIMAX-H3提速500高画质」）做分层核查的通用方法——把应用名里的宣称拆成可验证命题，按「名称声明 → 镜像静态信息 → 实例体检 → 运行时配置 → 受控实测 → 结论分层」逐层推进；用四级证据区分「宣称 / 源码配置 / 实测 / 未核实」，复用 autodl-remote-shell 的 SSH 接线、体检脚本、测速规约与规格台账，不重复造轮子。也覆盖「拿到镜像后怎么用」：先找作者自带说明（开始.html / API接口说明 / 镜像说明 / 面板地址），再判定 UI 还是 API、定位入口端口、验证通路、取产物、实际打开界面截图做使用教程。This skill should be used when 用户给出 AutoDL 社区镜像应用名或链接、要求判断「提速 / 高画质 / 某版本」是否属实、需要多镜像同口径横评、要把一次实证拆成方法层与实例层，或问「这个镜像怎么用 / 在哪找说明 / 走 UI 还是 API / 怎么拿产物 / 怎么打开界面截图」。
agent_created: true
---

# AutoDL 社区镜像分层分析

## Overview

把社区镜像的「营销名」翻译成可验证命题，再逐层给证据。核心不是跑一次，而是让结论可分层复用：
**方法进 skill、固有规格进规格卡、实例数值留项目台账；宣称与实测永不混写。**
分层的收益是：每层可独立消费、可随时停在任一层、换镜像时只重跑受实例影响的那几层。

## 何时使用

- 用户给出形如「MINIMAX-H3提速500高画质」的应用名，问「这个镜像怎么样 / 值不值得租」
- 要验证名称里的数字或形容词（提速 500、高画质、长上下文等）是否真在实例上成立
- 多个社区镜像要横评，需要统一口径与证据等级
- 已有一次实证，要拆成「通用方法 / 固有规格 / 一次性数值」三层沉淀
- 拿到别人给的镜像或已开好的实例，问「怎么用 / 在哪找说明 / 走 UI 还是 API / 怎么拿产物 / 怎么打开界面截图做教程」

## 与其它 skill 的分工（不要重复实现）

| 需要什么 | 去哪拿 |
|---|---|
| SSH 连接、MCP 接线、命令通道 | `../autodl-remote-shell/references/setup-workbuddy-shell-mcp.md`、`shell-mcp-usage.md`；本 skill 只给快速入口，见 `references/ssh-connection.md` |
| 新实例体检 | `../autodl-remote-shell/scripts/autodl-instance-probe.sh` |
| 下载 / 端口 / 交付 | `../autodl-remote-shell/references/autodl-download.md`、`autodl-ports.md`、`autodl-delivery-modes.md` |
| 测速三前提与已完成的提速实证 | `references/existing-analysis.md`（指针，不复制正文） |
| 四级证据与规格台账 | `references/evidence-and-ledger.md`（指针 + 最小字段） |
| 从基础镜像还原：装什么、模型怎么下 | `references/environment-recipe.md`、`references/model-recipe.md`（探测 → 实测 → 还原动作；实例数值留项目） |
| 拿到镜像后怎么用：找自带说明、UI/API 入口、取产物 | `references/usage-guide.md`（先找作者现成说明，再探测；端口/域名/工作流名留项目） |

## 工作流决策树

```
开始
 ├─ 只有应用名、还没连实例 → L0 拆声明（references/layered-method.md 第一节）
 │    ⚠ 名称里的数字与形容词都是「宣称」，未实测前一律标「官方 / 未核实」
 ├─ 需要连 SSH → 读 references/ssh-connection.md：先探测 shell MCP 是否已接；
 │    未接就用 paramiko 兜底（凭据运行时注入，不写入任何文件）
 ├─ 连上了但没体检 → 复用 autodl-instance-probe.sh；结果留项目台账
 ├─ 问「镜像怎么用」→ 读 references/usage-guide.md：
 │    ① 先找作者现成说明（开始.html/API接口说明.html/镜像说明.md/面板地址.txt）——最高优先级；
 │    ② 再看进程/端口定入口，端口用 lsof（ss 可能不存在）；
 │    ③ API 先看作者封装接口（/api/workflow/generate 等），原生 {"prompt": ...} 留作精细控制；
 │    ④ 端口/域名/工作流名是实例数值，不假定默认值
 ├─ 要验「提速」→ L3 查运行时参数 + L4 受控实测（预热 / 换数据 / 交叉对照，缺一不算）
 ├─ 要验「高画质」→ L3 查模型版本与量化档 + L4 固定输入输出对照 + 同时间点截帧并排看
 ├─ 提交工作流报错 no_prompt → 是请求体缺 {"prompt": <工作流>} 外层，不是镜像故障
 ├─ 要还原到基础镜像 → 读 references/environment-recipe.md 与 model-recipe.md；
 │    先查 git remote/commit、节点是否整包、pip freeze 里的 file:// 行、模型软链目标，再决定装/链/下
 ├─ 磁盘不够 / 要扩数据盘 → 读 environment-recipe.md「盘位与扩容」：先 df 判定落哪块盘、ro 就换目录；软链勿动，只迁实体
 ├─ 要估算显存 / 判断某卡能否跑 → 读 minimax-h3-model-variants.md 第八章：分文件体积 / GPU 常驻 / 运行峰值三个数
 ├─ 多个镜像对比 → 复制 scripts/analysis-ledger-template.json，按四级证据填
 └─ 出结论 → 声明与实测分栏写；成立条件与不成立条件成对写
```

## 不可违背的硬规则

1. **凭据不进 skill、不进项目文件、不进报告。** 示例只写占位符；运行时从环境变量或手动输入注入，用完清除。
2. **应用名的每个数字与形容词都是「声明」。** 没有 L4 受控实测，不得写成「实测」。未定义单位的数字先标「口径未定义」。
3. **分层不可跳级。** 没做 L2 体检就别下 L4 性能结论；实例相关数值（磁盘余量、PID、单次 tok/s）留项目，只有通用方法与固有规格才可进 skill。
4. **测速三前提缺一作废**：预热 3~5 次取后几次、每次换数据、A→B→A→B 交叉对照（实证见 `references/existing-analysis.md`）。
5. **不写死维度清单，不写「某类镜像必查某几项」。** 维度按当前问题临时组织；举例只演示拆法，不构成规则。
6. **发到远端的脚本正文保持只读**：不放 `rm -rf`、`pkill`；长任务后台化 + 落日志 + 轮询（见 `shell-mcp-usage.md`）。
7. **结论成对写**：成立条件 + 不成立条件；查不到写「未核实」并留空，不补猜。
8. **还原配方只写「实测命令 → 实测结果 → 还原动作」。** 自定义节点没有 `.git` 时不得编造 GitHub 地址；`pip freeze` 的 `file://` 行不是可移植清单；模型先判软链再决定下载，不把一次性路径写成通用规格。
9. **评论区里的坑必须回填进 skill，不算"实例数值"。** 报错的真实归因（如 `no_prompt` = 请求体格式）、对照要拉平的具体字段、截帧取哪个目录、本机回显导致临时文件含密——这些是**通用动作**，留在一项目报告里等于没沉淀。判据：换一个镜像还会不会遇到？会 → 进 skill。
10. **磁盘与显存分开算。** `/root/autodl-tmp` 不保证是数据盘，先 `df -h` 实证；软链权重不算实体占用，迁移只动实体；显存看 `/system_stats`、磁盘看 `df -h`，不互相推断。
11. **使用入口先找现成说明、再实测，不靠默认。** 优先级：作者自带说明（HTML/md）> 进程 cmdline > 端口。端口用 `lsof -nP -iTCP -sTCP:LISTEN`（`ss` 可能 `command not found`）。端口、面板域名、工作流名、输出目录是实例数值，只留项目；skill 保留"怎么找到它们"的方法。UI 与 API（作者封装 + 原生框架）各自验证，不写死一种用法。
12. **认证失败先分层诊断、失败即停，不连续重试。** TCP → host key 指纹 → 认证三层定位：TCP 通但认证拒，很可能是临时状态，**不写"凭据已失效"结论**，也不反复试（触发风控）。读到 API 端点不等于端点可用，实调返回数据才算实测。

## 资源索引

### references/

- `layered-method.md` — 通用六层方法（L0~L5），每层给入口条件 / 判定问题 / 判定动作 / 退出条件 / 常见误判；L4 含 no_prompt 归因、同时间点截帧、A/B 帧数对齐、**一次 A/B 只是上界（预热+交叉+换数据）、显存峰值采样、同结构只切开关的画质对照**
- `ssh-connection.md` — 连接远程 SSH 的三条路径、安全约定（含 PowerShell 回显含密临时文件必须删并验证）、**认证失败的三层诊断（TCP / host key 指纹 / 认证，失败即停不重试）**、一次已实测连接（含本机环境事实）
- `evidence-and-ledger.md` — 四级证据、台账最小字段、按消费时点分视图（详细规约指向 capability-matrix.md）
- `existing-analysis.md` — 「目前已经的分析」索引与可复用边界（指针，不复制正文）
- `environment-recipe.md` — 从基础镜像还原：系统/驱动/Python/Torch、git remote+commit/tag/工作区补丁、节点 bundle、依赖坑、启动命令；末章「盘位与扩容」给 df 判定 / ro 检测 / 软链勿动的迁移三步；同一问题给「主路径 + 备选 + 适用条件」
- `model-recipe.md` — 从工作流反查模型、软链目标、下载方式的判定问题、放置目录与校验；含「模型区别说明」五字段模板与取证动作
- `minimax-h3-model-variants.md` — 先看文件名缩写对照（任务前缀 fl2va/ref2va + 精度后缀 + 组件词），再是 H3 家族固有规格：DiT/TE/VAE/LoRA 各档区别、硬件要求、ConvRot 与 8-step 的坑；第八章是**显存估算**（文件体积≠峰值、各档显存表、分辨率/帧数/TE 换出/VAE 解码等影响因素、实测命令）；实例数值不进此文件
- `usage-guide.md` — 拿到镜像怎么用：先找作者现成说明（开始.html/API说明/镜像说明，含剥 HTML 标签读正文）、四个判定问题、端口用 lsof（ss 可能不存在）、作者封装 API 优先于原生 `{"prompt": ...}`、取产物位置、反面做法；端口/域名/工作流名属实例数值，只留项目

### scripts/

- `analysis-ledger-template.json` — 空台账模板，复制到项目目录再填；占位示例仅说明结构，不是结论清单
