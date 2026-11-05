# WorkBuddy 接入 shell MCP（远程执行 shell）

## 目的

让 WorkBuddy 获得一个**可执行 shell 命令**的工具能力，并且既能在本机执行，也能通过 SSH 到远程服务器（如 AutoDL GPU 实例）执行。
接线完成后，WorkBuddy 侧会出现两个延迟加载工具：`mcp__shell-mcp__execute_command` 与 `mcp__shell-mcp__get_tools`。

本文记录的是**一次真实接线的完整步骤**，包含过程中实际踩到的坑与修正方式。

---

## 快速流程总览

```
① 拉仓库 → ② 建隔离 venv 并装依赖 → ③ 确认真实入口
→ ④ 配置 config.json（黑名单模式）→ ⑤ 合并进 ~/.workbuddy/mcp.json
→ ⑥ 在连接器管理页点「信任」→ ⑦ ToolSearch 取 schema → ⑧ DeferExecuteTool 调用
```

---

## 步骤 1：获取 shell-mcp

选用第三方实现 `cnchef/shell-mcp`（基于 MCP 的 shell 接口，支持本地 + SSH 远程执行）。

建议用 codeload tarball 方式拉取（GitHub 仓库一律优先用这种方式，原因见 `autodl-download.md`）：

```bash
curl -sSL https://codeload.github.com/cnchef/shell-mcp/tar.gz/refs/heads/main -o shell-mcp.tar.gz
tar xzf shell-mcp.tar.gz
```

> 若本机 GitHub 直连顺畅，也可用常规 clone，但国内环境建议一律走 tarball。

## 步骤 2：建隔离 venv 并安装依赖

在仓库同级建立**独立虚拟环境**，不要污染本机环境：

```bash
cd /g/Code/shell-mcp
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

### 两个必踩的安装坑

1. **pip 镜像失效**：本机若配置了清华镜像且已失效，会报 `from versions: none`。
   此时显式指定官方源：

   ```bash
   .venv/Scripts/python.exe -m pip install --index-url https://pypi.org/simple <包名>
   ```

2. **Python 3.13 与 `asyncio`**：老 `requirements.txt` 里若有 `asyncio>=x` 行会安装失败——
   Python 3.13 中 asyncio 已是标准库。**直接删掉该行**，改装真实依赖：

   ```bash
   .venv/Scripts/python.exe -m pip install aiohttp paramiko bcrypt cryptography
   ```

## 步骤 3：确认真实入口（重要）

**README 里写的入口文件名可能是错的。** 实际入口是 `shell_mcp_server.py`。

先确认其运行方式与默认参数：

```bash
.venv/Scripts/python.exe shell_mcp_server.py --help
```

实测结论：`--mode` 默认 `stdio`，`--config` 默认 `config.json`。
采用 **stdio 模式**（本机最简、无需额外起服务）。

## 步骤 4：配置 config.json（安全策略）

在仓库根目录的 `config.json` 中：

- 采用**黑名单模式**（配置里的多条过滤规则），而非白名单
- **保留空的 whitelist**（表示不限制命令白名单，仅由黑名单兜底）
- 执行器限制为**仅 localhost 可执行**
- **务必删除示例里的明文密码**

> 这份黑名单之后会直接影响可用命令，务必先读一节「黑名单行为」。

## 步骤 5：合并进 WorkBuddy 的 MCP 配置

配置文件路径是 **`~/.workbuddy/mcp.json`**（注意**没有**前置点号，不是 `.mcp.json`）。

> **必须合并进已有的 `mcpServers` 对象，不能整体覆盖**——否则会清掉其它 MCP server。

追加的条目形态：

```json
{
  "mcpServers": {
    "shell-mcp": {
      "command": "<本地仓库路径>/.venv/Scripts/python.exe",
      "args": [
        "<本地仓库路径>/shell_mcp_server.py",
        "--mode", "stdio",
        "--config", "<本地仓库路径>/config.json"
      ],
      "disabled": false
    }
  }
}
```

`<本地仓库路径>` 换成实际的 shell-mcp 解压位置后，再按操作系统调整：

- **Windows**：`command` 指向 venv 内的 `python.exe`；且 JSON 里的反斜杠要转义（写成 `\\`），例如
  `"command": "D:\\tools\\shell-mcp\\.venv\\Scripts\\python.exe"`
- **Linux / macOS**：`command` 指向 `<本地仓库路径>/.venv/bin/python`，路径用正斜杠

## 步骤 6：在连接器管理页「信任」

写完配置**不会自动生效**。需到 **连接器管理页面**，在新出现的自定义连接器条目右侧点击 **「信任」/ Trust**，才会实际启用。

用户反馈过的典型情况：「我已经点了信任啊！！！」——若工具仍未出现，回头确认：
- `mcp.json` 是否被其它条目覆盖破坏
- `command` 路径是否真实存在（venv 是否已建好）

## 步骤 7~8：调用方式

shell-mcp 提供的工具属于**延迟加载（deferred）工具**，不在常驻工具列表里，必须先查 schema 才能调用：

1. `ToolSearch` 以 `tool_names: ["mcp__shell-mcp__execute_command"]` 精确取 schema
2. 取到后通过 `DeferExecuteTool` 传入参数调用

### execute_command 参数说明

| 参数 | 必填 | 说明 |
|---|---|---|
| `command` | ✅ | 要执行的命令 |
| `host` | — | 远程主机地址；不提供则本机执行 |
| `port` | — | SSH 端口，默认 22。AutoDL 实例通常是 1xxxx~3xxxx 的高位端口 |
| `username` | — | SSH 用户名，远程执行时通常必填 |
| `password` | — | SSH 密码（或用 `key_file` 免密） |
| `key_file` | — | SSH 私钥文件路径 |
| `session` | — | 会话名，默认 `default` |
| `cwd` | — | 执行目录，影响相对路径命令 |
| `env` | — | 额外环境变量（对象） |
| `force_execute` | — | 强制跳过安全检查执行危险命令，默认 `false` |

### 最简调用形态

```json
{
  "command": "nvidia-smi",
  "host": "<实例SSH主机，从控制台的登录指令获取>",
  "port": 22,
  "username": "root",
  "password": "<实例密码>"
}
```

> `port` 为**整数**类型。AutoDL 实例通常是 1xxxx~3xxxx 的高位端口，把示例里的 `22` 换成控制台给出的实际端口。

---

## 附录 A：黑名单行为（实测）

过滤规则会给整条命令做**字符串匹配**，命中则**整条命令一行都不执行**，并返回「命令被拒绝执行」。

已知会命中的模式：

| 模式 | 表现 |
|---|---|
| `^\s*pkill` | 命令只要**以 pkill 开头**就被拦。杀进程改用 `kill <PID>` |
| `.*;\s*rm\s+-rf` | **`rm -rf` 前有 `;` 分隔**时被拦（即复合命令中的 `rm -rf`）。单独一条且不以 `;` 开头的 `rm -rf <path>` 可放行 |

**实践建议**（两条组合使用，基本可实现零删除操作）：

- 优先「换新目录名」而非删旧目录
- 单个删除操作单独成条命令发起，不要拼接在长脚本里

## 附录 B：命令回显被吞（空输出）

**现象**：执行耗时长、或含多个网络超时的命令时，工具返回**完全为空**（无 stdout、无 stderr、无退出码），看起来像命令失败，实则可能仍在运行或已超时被放弃。

**规律**：秒级返回的命令正常有回显；超过工具响应窗口的命令容易返回空。

**对策**：长任务一律后台化（详见 `shell-mcp-usage.md`），用日志文件承接输出并轮询。
