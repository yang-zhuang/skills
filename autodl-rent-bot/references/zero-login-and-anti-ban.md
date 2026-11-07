# 零登录与防封号：原理、参数与红线

## 目的

让浏览器机器人**以真人已登录的状态**进入 AutoDL，并让长达数小时的蹲守在平台日志里
看起来像「一个人开着页面挂机」，而不是自动化脚本。这是整个项目里最不能妥协的部分：
账号里有余额、有已租实例，封号的代价远大于抢不到一张卡。

---

## 一、零登录：为什么必须复制 profile

### 结论先给

不要直接把 `AUTODL_USER_DATA_DIR` 指向 Chrome 默认 profile 路径去启动 Playwright。
正确做法是：**先用 `robocopy` 把真实 Chrome 的 profile 复制到非默认路径，
再用这份副本启动** `launch_persistent_context`。

`profile_util.py` 的 `ensure_profile_copy()` 就是干这个的：

- `_SKIP_DIRS` 跳过 20+ 个缓存/指标目录（Cache、Code Cache、GPUCache、Service Worker…），
  体积从数 GB 降到几十 MB；
- `robocopy /E /R:0 /W:0 /COPY:DAT`，退出码 `>=4` 只打印警告不中断；
- 复制完删掉副本里的 `SingletonLock` / `SingletonCookie` / `SingletonSocket` 锁文件；
- 副本已存在则直接复用（`force=True` 可强制重拷）；
- `subprocess.run(..., text=True, errors="replace")`——robocopy 输出含非 UTF-8 字节，
  不加 `errors="replace"` 读线程会抛 `UnicodeDecodeError`。

### 两个必踩的坑（根因）

| 现象 | 根因 |
|---|---|
| `其他程序中的会话已打开` / profile 被锁 | Chrome 正在运行时独占默认 profile。复制出副本后，两者互不干扰，**无需关闭真实 Chrome**。 |
| `DevTools remote debugging requires a non-default data directory` | Chrome 明确拒绝在**默认 profile 路径**上开启远程调试；Playwright 正是用 `--remote-debugging-pipe` 驱动浏览器的。副本路径非默认，绕开此限制。 |

`launch_persistent_context` 是 **`BrowserType` 的方法**（`p.chromium.launch_persistent_context(...)`），
不是 `Browser` 的方法；先 `p.chromium.launch()` 再调它会 `AttributeError`。
另外必须给它 `timeout=20000`，否则 profile 异常时会一直卡住。

### 登录态到底存在哪

**在 localStorage，不在 cookie。** AutoDL 的 JWT 键是 `token`，
位于 `Default\Local Storage\leveldb\000003.log` 里（形如 `token` 后跟 `eyJ...`），
payload 含 `uid` / `exp`，**约 60 天有效**。
profile 里那 7 个 `.autodl.com` cookie 全是百度统计 / 谷歌分析埋点，没有登录令牌。

推论（很重要）：

- 「Chrome 里保存了密码」 ≠ 「保持登录」。保存密码只是自动填表单，登录靠 JWT。
- 令牌过期后必须**在真实 Chrome 里重新登录一次**，然后删掉 `chrome_profile/` 让脚本重新复制。
- `AUTODL_COOKIE_FILE` 那套「保存 cookies 复用」的普通模式对 AutoDL 并不完全可靠，
  因为关键令牌不在 cookie 里——所以默认推荐零登录方案。
- `verify_token.py` / `check_localstorage.py` / `dump_realtime.py` 就是用来读这个 JWT、
  确认副本是否带着有效登录态的诊断脚本。

### 启动参数（`power_on_poll.py` 实测配置）

```python
context = p.chromium.launch_persistent_context(
    user_data_dir=profile_dir,          # 副本路径，绝不是 Chrome 默认路径
    channel="chrome",                   # 用本机真实 Chrome，不是 Playwright 自带 chromium
    headless=config.HEADLESS,
    slow_mo=150,                        # 拉长操作间隔，降低机器特征
    timeout=20000,
    viewport={"width": 1920, "height": 1080},
    locale="zh-CN",
    user_agent=REAL_UA,                 # 真实 Chrome UA，不带 headless/chromium 标记
    args=[
        "--window-size=1920,1080",
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--no-sandbox",
    ],
    ignore_default_args=["--enable-automation"],  # 去掉 --enable-automation 启动参数
)
context.add_init_script(STEALTH_SCRIPT)
```

`STEALTH_SCRIPT` 在页面脚本执行前注入，做四件事：覆盖 `navigator.webdriver` 为 `false`、
删除 CDP 注入的 `cdc_*` 属性、补齐 `window.chrome` / `navigator.plugins` / `navigator.languages`、
抹掉 UA 里的 `HeadlessChrome` 字样。不做这层，极验滑块会识别出 Playwright，
**即便人工拖动也会判定失败**。

---

## 二、防封号：把「等待」做成近乎零请求

### 设计目标

稀缺机型（如 `vGPU-48GB-350W`）可能连续十几个小时显示「该主机 GPU 不足」。
如果每 60 秒点一次「开机」，十几个小时就是几百次无效请求，风控特征非常明显。
所以核心不是「怎么更快地点」，而是**怎么在没机会的时候一次都不点**。

### 四层措施

**1. 只在一个页面上待着**
整页 `goto` 只在开局做一次（`/console/instance/list`）。之后每轮用
`parse_instance_rows(page)` **原地重新解析当前 DOM** —— AutoDL 的列表本身会 JS 自动刷新，
不需要机器人去刷。**绝不导航到算力市场**（那是操作最密集、风控最敏感的区域）。

**2. `.gpuTips` 只读信号 + 零点击轮询**
AutoDL 只在**主机有空闲资源**时，才给「已关机」实例行渲染 `.gpuTips` 标记
（`parse_instance_rows` 的 `gpu_tips` 字段）。所以：

- 有标记 → 立刻点「开机」（机会窗口可能只有几秒）；
- 无标记 → **本轮零点击**，纯本地读 DOM + 等待，对平台零请求；
- 每 `PROBE_EVERY = 5` 轮做一次兜底试探点击，防止「标记与实际不一致」时永远开不了。

实测效果：请求量降到约 **1/5**。

**3. 随机抖动**
`jitter_wait(base)` = `base * random.uniform(0.85, 1.25) + random.uniform(0, 10)`。
固定间隔在服务端日志里是标准机器节奏；随机后每轮可能 48 秒、53 秒、61 秒……
更像人手动刷新。**基准间隔不要低于 45 秒**，长时间蹲守建议 60 秒。
典型调用：`python power_on_poll.py <实例ID> 700 60`（约 13.5 小时）。

**4. 失败静默 + 自恢复日志**
按钮 disabled / 不存在 → 静默跳过，不报错、不重试点击。
`log()` 每行 `flush()` 写 `power_on_poll.log`，进程被上层强杀也能从日志续看进度；
轮询是**无状态**的，随时停、随时重启，不丢任何进度（反正只在成功时才停）。
所有异常都在轮内捕获，截图后继续下一轮。

### 实战数据（可复现的量化基线）

| 项目 | 数值 |
|---|---|
| 机型 / 实例 | `vGPU-48GB-350W`，西北B区 / B55机 |
| 第一轮起跑 | 2026-10-21 16:28 |
| 开机成功 | 2026-10-22 02:14:01（第 265 轮，耗时约 9.75 小时） |
| 期间主机状态 | GPU 空闲数始终 0 卡，直到 02:13:28 第 265 轮兜底试探时才腾出 |
| 试探点击总数 | 约 48 次 / 4.5 小时；整晚约 140 次，摊到每小时约 11 次 |
| 成功率 | 一次命中，02:13:31 弹计费确认框 → 02:13:39「开机中」→ 02:14:01「运行中」 |
| 账号状态 | 全程无风控异常、无验证码、无登录触发 |

**推论**：稀缺共享主机的腾卡时机来自**其他用户关机/释放**，与本地几点钟无关，
但深夜命中概率更高。所以「延长时间」比「提高频率」有效得多，且风险更低。

---

## 三、红线（违反任一条就不要跑）

1. **不把登录交给脚本**。登录/极验滑块是封号最高危动作，人工在真实 Chrome 里登录一次即可。
2. **不刷算力市场**、不高频 `page.goto`、不把基准间隔调小到 10 秒以内。
3. **不点「克隆实例」**（新建计费实例）、**不点「无卡模式开机」**（无 GPU）、
   **不点「创建并开机」** 除非人工闸门通过。
4. ⚠️ **`power_on_poll.py` 会自动点「确定」确认开机计费框**（`SAFE_BUTTONS` 白名单含
   「确定 / 确认 / 立即开机」）。只有在用户明确授权「把这台机器开机」时才跑；
   要改成无人值守通用版，先给计费确认框补人工闸门。
5. **不提交登录态**：`.env`、`chrome_profile/`、截图、日志一律不进 Git。
6. 浏览器窗口可以最小化，**别关**（关了进程就停）；别让电脑睡眠/休眠。

---

## 四、换机器适配清单

| 要改什么 | 在哪 |
|---|---|
| `.env` 里的账号、`AUTODL_USER_DATA_DIR` | `scripts/.env.example` → 复制为 `.env` |
| Chrome profile 路径 | `check_profile.py` / `dump_realtime.py` / `scan_all_profiles.py` / `verify_token.py` 已改为按 `os.path.expanduser("~")` 推导，`config.py` 注释只是示例；仅当 Chrome 数据目录不在标准位置时才需改 |
| 实例 ID | `power_on_poll.py` 的 `TARGET_ID`、`power_cycle_once.py` 的 `TARGET_ID`、<br>`inspect_buttons.py` / `inspect_expand*.py` 的 `TARGET_ID`、各脚本文档字符串示例 |
| Python 环境 | 只要装了 `playwright` + `python-dotenv` 且有本机 Chrome；本机实战用 conda `py12` |
| **非 Windows** | `profile_util.py` 的 `robocopy` 必须换成 `shutil.copytree` + 忽略列表，并自行处理锁文件 |

> 上游 README 写「支持 Windows / macOS / Linux」是不准确的——`robocopy` 是 Windows 专用。
> 另外 `config.py` 第 111 行注释「运行时需先关闭正在占用该 profile 的 Chrome 实例」也是
> 过时说明：副本方案并不需要关 Chrome。以 `profile_util.py` 的实际行为为准。

---

## 附录：根因与排错

- **弹窗一直不消失、页面点不动** → 不是风控，是弹窗遮罩层拦截了一切点击。
  见 `dom-and-dialogs.md` 的「15 个隐藏 dialog」坑与「我知道了」按钮。
- **开机一直失败、状态永远「已关机」** → 该主机 GPU 空闲 0 卡，不是脚本坏了。
  弹窗会明确写「实例GPU需求数量：1 卡，主机GPU空闲数量：0 卡」。
- **登录态突然失效** → JWT 约 60 天过期。真实 Chrome 重新登录 → 删 `chrome_profile/` → 重跑。
- **Chrome 起不来 / 卡死** → 检查副本是否被占用（有没有残留的 `chrome.exe --user-data-dir=...chrome_profile` 进程），
  必要时按 PID 结束；`launch_persistent_context` 一定要带 `timeout`。
- **怎样确认副本真的带着登录态** → 跑 `python verify_token.py`，它会从 leveldb 里
  正则抓 `token` 后的 JWT 并解析 payload。
