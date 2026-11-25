# 连接 AutoDL 远程 SSH（快速入口）

完整接线与踩坑根因在 `../autodl-remote-shell/references/setup-workbuddy-shell-mcp.md` 与 `shell-mcp-usage.md`。
本文只做**路由**：先探测哪条通道可用，再按对应方式连；三条路径按优先级排列。

## 安全约定（先读）

- **不要把密码写进 skill、脚本、报告、台账或聊天记录里的持久化文件。** 用户粘贴的明文密码只在当次运行时使用。
- 自动化通道统一用环境变量注入密码，用完清除；示例里只出现占位符。
- **本机 PowerShell 回显陷阱**：native 命令的 stderr 被包装成错误时，会把命令行里的密码片段一起回显进 `Out-File` 的输出文件。缓解：加 `2>$null`、输出前 `sys.stdout.reconfigure`；但**凡命令行带过密码的临时输出文件一律按含密处理，用完立刻删**（本次每次采集后都清理了 `_tmp_*`）。校验清理是否彻底，别只看脚本删了没、要列出剩余文件确认。
- 远端命令保持只读；杀进程用 `kill <PID>`，不用 `pkill`；脚本正文不放 `; rm -rf`（会被 shell MCP 安全过滤整条拒绝）。

## 路径 A：shell MCP（优先）

1. 先用工具检索探测 `mcp__shell-mcp__execute_command` 是否已接。已接则直接进入第 3 步。
2. 未接：按 `setup-workbuddy-shell-mcp.md` 接线——拉 `cnchef/shell-mcp` → 建隔离 venv → 装 `aiohttp paramiko bcrypt cryptography` → 配 `config.json`（黑名单模式、空 whitelist、删示例密码）→ **合并**进 `~/.workbuddy/mcp.json`（不要覆盖其它 server）→ 在连接器管理页点「信任」。
3. 通过延迟加载工具调用，参数形态（`port` 必须是整数）：

```json
{
  "command": "nvidia-smi",
  "host": "<控制台给出的 SSH 主机>",
  "port": <控制台给出的端口整数>,
  "username": "root",
  "password": "<运行时注入，不落盘>"
}
```

每次调用都是全新非交互 shell：需要 Python 时先 `export PATH=/root/miniconda3/bin:$PATH`；
需要境外源时才 `source /etc/network_turbo`。长任务一律后台化 + 落日志 + 轮询。

## 路径 B：本机 Python + paramiko（MCP 不可用时的兜底）

**本机现状（2026-11-25 实测）**：`D:\Ananconda3\python.exe`（base，Python 3.9）已装 paramiko；
`C:\Users\18372\.conda\envs` 下 7 个环境（dcode / hunyuanvideo / paddleocr / py12 / qwen-tts / tradingagents / webwalker）**都没有** paramiko。
因此兜底脚本直接用 base 解释器，不必新建 venv。

要点：`look_for_keys=False, allow_agent=False`，否则 paramiko 会扫 `~/.ssh` 私钥被本机权限系统拒；
远端命令含 `$` / `$(...)` 时用带引号定界符的 heredoc，避免本地展开。

最小可运行示例（密码从环境变量读，不写进文件）：

```python
import os
import paramiko

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(
    os.environ["AUTODL_SSH_HOST"], port=int(os.environ["AUTODL_SSH_PORT"]),
    username="root",
    password=os.environ["AUTODL_SSH_PASS"],
    look_for_keys=False, allow_agent=False, timeout=20,
)
stdin, stdout, stderr = client.exec_command("nvidia-smi", timeout=15)
print(stdout.read().decode(errors="replace"))
client.close()
```

PowerShell 里临时注入：`$env:AUTODL_SSH_PASS = '<运行时输入>'`，用完 `Remove-Item Env:\AUTODL_SSH_PASS`。
base 环境的 paramiko 会打一行 `CryptographyDeprecationWarning: Blowfish has been deprecated`，不影响连接。

## 路径 C：Windows OpenSSH 手工登录（不适合自动化）

本机有 `C:\Windows\System32\OpenSSH\ssh.exe`。人工排查时可直接：

```
ssh -p <端口> root@<控制台给出的 SSH 主机>
```

然后交互输入密码。适合临时看一眼，不适合脚本与批量分析。

## 连通性判定

- MCP 连续空返回 ≠ 远端挂了。先用路径 B 绕开 MCP 直连，能连就是本地 MCP⇄SSH 链路卡死；
  空返回期间发出的命令可能已在远端执行，先探测再决定是否重发。
- 连上后先做 L2 体检再谈镜像结论，体检脚本在 `../autodl-remote-shell/scripts/autodl-instance-probe.sh`。
- **认证失败不等于实例关了或密码变了**：同一组凭据在数小时内可能先失败、后成功（实测本轮
  `AuthenticationException: Authentication failed.` 连续出现，隔一段时间同一密码又能连）。
  遇到失败按三层定位，别急着下"凭据失效"结论：
  1. **TCP 层**：`socket.create_connection((host, port))` + 读 banner——确认端口在监听、不是网络断。
  2. **host key 层**：连上后取 `get_remote_server_key()` 的 SHA256 指纹，和上次记录比对——
     指纹变了才是实例重建，没变就是同一台。
  3. **认证层**：最后才看 `auth_password` 的异常。失败时**停止重试**（连续失败会触发登录风控），
     告知用户"TCP 通、指纹未变、认证被拒，可能是临时状态或密码变更"，让用户决定重发密码或稍后再试。
  分层脚本见项目 `scripts/ssh_diag.py`；`allow_agent=False, look_for_keys=False` 必须带，
  否则本机私钥会被尝试、掩盖真实认证结果。

---

## 附录：一次已实测连接的方法要点（实例数值留项目）

2026-11-25 用路径 B 对某 AutoDL 实例做过只读探测，证明通道可用。
**具体 hostname / 域名 / GPU / 磁盘数值属实例一次性数据，留在项目台账，不写进 skill。**
可迁移的要点只有两条：

- 用 paramiko（base 环境已装）可直连，不必新建 venv。
- `df -h` 的落点可能与直觉相反（当时 `/root/autodl-tmp` 落在 overlay 而非数据盘）——
  这正是 L2 必须实测的理由，不是某台机器的规格。
