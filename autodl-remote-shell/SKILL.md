---
name: autodl-remote-shell
description: 通过 shell MCP（如 cnchef/shell-mcp）远程操控 AutoDL / 恒源云 / SeetaCloud GPU 算力实例的完整工作方法——涵盖 MCP 接线、远端命令执行、新实例体检、GitHub 代码拉取、模型权重下载、推理引擎编译安装，以及实例内服务经 6006/6008 端口对外访问。This skill should be used when 用户要求连接或操作远程 AutoDL 实例、排查显存/内存/磁盘配额、下载 GitHub 仓库或模型权重到实例、编译安装推理引擎（如 Strata、llama.cpp）、或需要让实例内的服务从公网访问时。
agent_created: true
---

# 远程操控 AutoDL 实例

## Overview

让本机 AI 客户端（WorkBuddy 等）通过 shell MCP 工具安全地 SSH 到 AutoDL GPU 实例执行命令，
并完成「拉代码 → 装依赖 → 下模型 → 跑服务 → 对外访问」这条完整链路。
本 skill 固化的是**经实操验证的路径与参数**，避免每次重新推导而踩回已知的坑。

## 何时使用

- 用户给出 `ssh -p <port> root@<host>` 形式的连接信息，要求连接/查看/操作远程实例
- 需要查询实例真实配额（显存、内存、CPU、磁盘），而非宿主机看到的虚值
- 需要在实例上拉取 GitHub 仓库、下载 ModelScope / HuggingFace 模型权重
- 需要编译/安装推理引擎并处理 toolchain 版本冲突
- 需要把实例内的服务（如推理 API、Gradio）暴露到公网访问

## 工作流决策树

先判断所处环节，再**只加载对应 references 文件**，避免一次性读入全部内容：

```
开始
 ├─ ① 本机还没有可用的 shell MCP 工具？
 │     └─ 读 references/setup-workbuddy-shell-mcp.md（接线，含真实参数与坑）
 │
 ├─ ② MCP 已通，要执行远端命令？
 │     └─ 读 references/shell-mcp-usage.md（参数表 + 长任务后台范式 + 黑名单）
 │
 ├─ ③ 面对一台新实例，要先摸清家底与连通性？
 │     └─ 用 scripts/autodl-instance-probe.sh（一次查完）
 │        + 读 references/autodl-platform.md（磁盘/配额纠偏/PATH/turbo/带宽）
 │
 ├─ ④ 要拉 GitHub 代码或下载模型？
 │     └─ 读 references/autodl-download.md（codeload tarball / ModelScope / no_proxy 分流）
 │
 ├─ ⑤ 要让实例内服务从公网访问？
 │     └─ 用 scripts/autodl-port-check.sh
 │        + 读 references/autodl-ports.md（6006/6008 映射规则与改端口方法）
 │
 └─ ⑥ 装/调 Strata（提速、开图片输入、加长上下文）？
       └─ 读 references/strata-speed-and-vision.md
          （worker 池对齐配额 3.7 倍 / vision 编译+mmproj+config / 256K 上下文）
```

## 不可违背的硬规则

以下为高频踩坑结论（详细根因与替代方案见对应 references）：

1. **模型权重必须放数据盘** `/root/autodl-tmp/<项目>`；代码放系统盘 `/root/code/<项目>`。
   系统盘通常仅 30G 且不可扩容，数据盘才可扩容。
2. **非交互 SSH 下 Python 不在 PATH**：每条命令前先 `export PATH=/root/miniconda3/bin:$PATH`。
   否则安装脚本会因找不到合适 Python 而试图 `apt install` 并卡在 `sudo: not found`。
3. **`source /etc/network_turbo` 仅对当前 shell 有效**，且主要用于 pip 装依赖与访问境外源；
   **ModelScope 等国内源不需要 turbo**。
4. **GitHub 代码不要用 `git clone`**：turbo 代理下 git 的 pack 协议会被限速到约 10 KB/s 并频繁 early EOF；
   ghproxy 类镜像同样会断。**改用 codeload tarball + curl**，实测 2.5–3.9 MB/s。
5. **模型下载走 ModelScope 国内源直连**；当同一进程里既要用 turbo（pip/引擎）又要用 ModelScope 时，
   用 `no_proxy=www.modelscope.cn` 精确分流，避免大模型流量被代理限速。
6. **长任务（下载/编译/安装）一律后台化**：`setsid ... > log 2>&1 &` 后轮询日志。
   前台执行超过工具窗口的命令会返回**空输出**，看起来像失败实则仍在跑。
7. **实例外网只暴露 6006 / 6008 两个端口**；服务必须监听这两个端口才能从公网访问。
   任何默认端口 8080/7860 的服务都**必须改成 6006**（详见 `references/autodl-ports.md`）。
8. **放远端执行的脚本正文必须保持只读**：不得包含 `rm -rf`（尤其带前置 `;`）、`pkill` 等，
   否则 shell MCP 的安全过滤会**整条命令一行都不执行**。杀进程用 `kill <PID>`。

## 资源索引

### scripts/

放远端 AutoDL 实例执行的只读诊断脚本（**不在本机双击运行**）。执行范式与逐项说明见 `scripts/README.md`。

- `autodl-instance-probe.sh` — 新实例体检：磁盘/真实配额/GPU/CPU/PATH/turbo/外网连通与测速
- `autodl-port-check.sh` — 检查 6006/6008 监听情况并给出公网访问结论

### references/

详细原理、参数表与踩坑根因，按需加载：

- `setup-workbuddy-shell-mcp.md` — WorkBuddy 接入 shell MCP 的完整步骤与配置要点
- `shell-mcp-usage.md` — execute_command 参数表、长任务后台范式、黑名单行为
- `autodl-platform.md` — 磁盘布局、cgroup 配额纠偏、network_turbo、带宽上限
- `autodl-download.md` — GitHub 代码与模型权重的下载方式与分流配置
- `autodl-ports.md` — 6006/6008 端口映射规则、公网地址格式、改服务端口的方法
- `strata-speed-and-vision.md` — Strata 提速（worker 池对齐 cpu.max，实测 3.7 倍）、开启图片输入（vision）、加长上下文到 256K 的完整步骤与排错
