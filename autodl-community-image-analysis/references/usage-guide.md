# 拿到镜像后怎么使用（通用方法）

> 目标：别人给一个镜像（或一台已开好的实例），在**不猜**的前提下找到它的使用入口，
> 判定该走界面还是 API，并验证通路真的可用。
> 这里只写**与镜像无关的探测方法**；具体路径、端口、工作流名是实例数值，留项目报告。

---

## 零、最高优先级：先找镜像作者自带的说明（不要自己猜）

**先找现成说明，再谈探测。** 社区镜像作者通常已经把使用说明、API 文档、面板地址放在家目录/镜像根，
自己瞎探端口和工作流是舍近求远。实测这台镜像的家目录就有三件套：

| 现成文件 | 内容 | 优先级 |
|---|---|---|
| `开始.html` / `*使用说明*` | 界面入口（如"从开机按钮后面的 WebUI-6008 入口打开"）、手动启动命令 | 最高，先读 |
| `*api接口说明*.html` / `*API*.md` | 作者封装的 API（可能比原生 ComfyUI API 好用得多）：提交、取结果、并发、文件上传 | 高，要自动化必读 |
| `*镜像说明*.md` | 工作流分类、选哪个工作流的决策表、版本与环境 | 高，选工作流必读 |
| `面板地址.txt` / `*面板*` | 管理面板 URL | 高，UI 入口 |

判定动作（只读）：

```
ls -la ~
find /root -maxdepth 3 -type f \( -iname '*.html' -o -iname '*说明*' -o -iname '*使用*' -o -iname '*面板*' -o -iname '*api*' \) 2>/dev/null | grep -v -E 'site-packages|node_modules|/.git/|custom_nodes/' | head -60
```

读 HTML 说明时先剥标签取正文（避免满屏 CSS）：

```
sed -e 's/<[^>]*>//g' -e 's/&nbsp;/ /g' <说明文件>.html | grep -v '^[[:space:]]*$' | head -120
grep -oE '<h[1-4][^>]*>[^<]{2,80}</h[1-4]>' <说明文件>.html | sed -e 's/<[^>]*>//g'
```

**写 HTML 教程的代码块时用 `<pre>`，不要用 `<div>` + CSS `white-space:pre-wrap`。**
预览面板会过滤 `<style>`，CSS 一没，`<div>` 里的换行被折叠成一行，命令和注释全挤在一起；
`<pre>` 的逐行显示是浏览器默认样式，不依赖 CSS。复制按钮放在 `<pre>` 外的 wrapper 里绝对定位。

**关键教训**：作者封装 API 与原生框架 API 是两套。实测镜像同时有原生 ComfyUI `POST /prompt`
和作者封装的 `POST /api/workflow/generate`（一步提交）、`GET /api/workflow/result?prompt_id=...`（取结果）、
并发批跑接口。做自动化前先读作者 API 文档，别只会用原生接口。

### 首次上手最短闭环（按顺序做，别跳步）

换任何镜像都按这条主线走，每步都有明确退出判据：

1. **连上 → 扫说明**：`ls -la ~` + `find -iname`（第零/二节）。**先读作者说明，别自己猜端口。**
2. **确认服务活着**：`lsof -nP -iTCP -sTCP:LISTEN` + `curl /system_stats`（第三节）。返回 200 才继续。
3. **拿到 UI 地址**：控制台映射 / 面板文件；用浏览器实际打开并截图（第三节）。
4. **跑通一条基础工作流**：加载标"基础版/官方"的预置工作流，第一次**只改提示词、不动模型**（第四节）。
5. **确认产物**：找到输出目录、能打开/下载（第六节）。**能拿到产物 = 镜像可用闭环。**
6. **要自动化才走 API**：先读作者封装接口，有一步提交就优先用；否则原生 `{"prompt": ...}`（第五节）。
7. **最后才做对照**：锁 prompt/seed/分辨率/帧数，预热 + A→B→A→B，同时间点截帧（见 `layered-method.md` L4）。

**退出判据**：第 5 步拿不到产物就停在该层排查，不要跳到第 7 步下性能结论。

---

## 一、先问对问题（别一上来就找 README）

拿到镜像先回答四个问题，答案决定后面查什么：

| 问题 | 为什么先问 | 判定动作 |
|---|---|---|
| 使用说明放在哪？ | 镜像作者习惯不同，没有统一路径 | 见第二节"从近到远找说明" |
| 有界面吗？入口是什么？ | 多数社区镜像是 Web UI（ComfyUI / Gradio / 面板） | 看第三节"进程与端口" |
| 能走 API 吗？ | 批量、自动化、可复现实验必须走 API | 看第四节"UI 还是 API" |
| 产物去哪取？ | 界面显示的和 API 返回的常常不是同一目录 | 看第五节"取产物" |

**常见误判**：以为"给了镜像就一定有 README"。实测镜像把说明放在自定义名字里
（如 `/root/zealman镜像说明.md`、`/root/面板地址.txt`），按标准名找会漏。

---

## 二、从近到远找使用说明（主路径 + 备选 + 适用条件）

### 主路径：先扫家目录与镜像根目录

在实例上执行（只读）：

```
ls -la ~
find /root -maxdepth 4 -type f \( -iname 'README*' -o -iname '*readme*' -o -iname '*说明*' -o -iname '*使用*' -o -iname 'MANUAL*' -o -iname 'GUIDE*' -o -iname '*.md' \) 2>/dev/null | grep -v -E 'site-packages|node_modules|/.git/|custom_nodes/.*/docs/' | head -80
ls -la /root/*.sh /root/autodl* 2>/dev/null
```

为什么这样写：

- `ls -la ~` 看根目录**有什么文件**，名字不标准也能看见（面板地址、说明文件常在此）。
- `find` 用 `-iname` 而非 `-name`，中文说明和大小写混排都能命中；
  `grep -v` 排掉依赖包和自定义节点里的大量无关 md，避免刷屏。
- `*.sh` / `autodl*` 找启动脚本，启动命令里通常藏着端口与参数。

### 备选路径（主路径找不到时用）

| 备选 | 命令 / 位置 | 什么时候用 |
|---|---|---|
| 看进程反推入口 | `ps -eo pid,etime,cmd \| grep -i -E 'comfy\|python\|nginx\|jupyter' \| grep -v grep` | 根目录空、但服务在跑——cmdline 会暴露端口和启动参数 |
| 看监听端口（**首选 `lsof`**） | `lsof -nP -iTCP -sTCP:LISTEN` | 不知道该连哪个端口。**实测踩坑：这台镜像 `ss: command not found`**，所以 `ss`/`netstat` 不能当默认假设；`lsof` 存在且给出 PID+端口+监听地址。找不到 `lsof` 再退 `cat /proc/net/tcp` | 
| 看进程反推端口 | `ps -eo pid,etime,cmd \| grep -i -E 'comfy\|python\|node\|jupyter'` | 端口查不到时，cmdline 里的 `--port` 就是答案（实测 6006/6007 都从这里读出） |
| 看工作台/面板 | `/root/面板地址.txt` 一类文件；AutoDL 控制台的"自定义服务" | 镜像自带管理面板（实测有 8443 面板） |
| 看预置工作流目录 | `find <镜像根> -maxdepth 3 -type d -iname '*workflow*'` | 说明只讲界面、没讲 API 时，工作流 JSON 就是 API 用例 |
| 看节点自带文档 | `custom_nodes/*/README*.md`、`docs/MODEL_DOWNLOADS*.md` | 说明文件太笼统，要具体模型/参数来源 |

### 退出条件

找到至少一项即可进入下一步：① 说明文件；② 面板地址；③ 能确定 UI/API 端口；④ 预置工作流目录。
四项都没有才标"镜像未给使用入口，需问作者"，不要脑补。

---

## 三、判定 UI 还是 API（不是二选一，是各自能用就都记）

### 怎么用浏览器实际打开 UI 并截图（实测可行）

镜像面板常有公网地址（如 `/root/面板地址.txt` 里的 URL）。拿到后**不要只写文字步骤，实际打开截图**：

1. 先验证可达：`requests.get(url, verify=False)` 看 HTTP 200 和 `<title>`。
2. 截图分两档：
   - 快速首页截图：Chrome 无头 `--headless=new --ignore-certificate-errors --screenshot=<路径> <url>`。
     **注意 `--screenshot=<路径>` 必须带引号整体传**，否则 PowerShell 下不生成文件。
   - 需要点击操作：用 Python Playwright（本机 `py12` 有包，浏览器内核在 `AppData\Local\ms-playwright`）。
3. Playwright 打开 ComfyUI 这类 canvas 应用的两个坑：
   - **不要用 `wait_until="networkidle"`**——有 WebSocket 永不 idle，必超时；用 `domcontentloaded` + 固定等待。
   - **不要用 `inner_text` 判断加载完成**——节点画布画在 canvas 上，文字读不到；看 `document.querySelectorAll('canvas').length` 和 DOM 元素数。
   - 启动动画期间截图只会得到启动画面，等 25~30 秒再截，`screenshot(animations="disabled")`。
4. 截图存项目目录 `images/`，在 HTML 教程里用相对路径 `images/xxx.png` 引用；每张图标注来源（本机实测 / 镜像自带），别混为一谈。
5. 控制台页面（AutoDL 的「访问应用服务」）本机浏览器登不进去，用镜像自带说明里作者截的图，注明来源即可，不要伪造。

### 判定问题

1. **有 Web 服务在监听吗？** → 有就有 UI 路径（`lsof -nP -iTCP -sTCP:LISTEN` 看端口；`ss` 可能不存在）。
2. **服务是 ComfyUI / Gradio 这类带 HTTP API 的框架吗？** → 是就有 API 路径。
3. **预置工作流是 JSON 吗？** → 是的话 API 路径通常可直接复用。
4. **能用只读端点验证吗？** → `/system_stats`、`/object_info`、`/queue` 返回 200 即通路可用。

### 验证动作（只读，不改实例状态）

```
curl -s http://127.0.0.1:<端口>/system_stats
curl -s http://127.0.0.1:<端口>/object_info | head -c 500
curl -s http://127.0.0.1:<端口>/queue
```

- `/system_stats` 返回版本号和显存 → 服务活着、API 可用。
- `/object_info` 返回节点字典 → 能列出可用节点，工作流可提交。
- `/queue` 返回队列状态 → 能读运行状态。

**注意**：端口不要假定默认。实测同一镜像同时有 6006（ComfyUI，仅 127.0.0.1）、6008（面板/node，HTTP 200）、
6007（tensorboard）、8888（jupyter）、2022（autopanel）、22（sshd）；外网面板是 8443 映射。
以 `lsof -nP -iTCP -sTCP:LISTEN` 与启动 cmdline 为准，`ss` 在这台镜像上不存在。

---

## 四、UI 路径怎么用（给普通用户的主线）

1. **拿到访问地址**：优先看镜像自带的 `开始.html` / 说明文件怎么写（实测本镜像明确要求
   "从开机按钮后面的 **WebUI-6008** 入口打开 ComfyUI 启动管理页面"）；其次看面板地址文件；
   再次才是 AutoDL 控制台 → 实例 → 自定义服务。只监听 `127.0.0.1` 的服务需控制台映射才能外网访问。
2. **打开界面**：浏览器打开映射后的地址；ComfyUI 就是标准节点画布。
3. **加载预置工作流**：界面里找"工作流 / Workflow"菜单，或拖入镜像自带的 `.json`；
   镜像通常会预置若干条，优先用标了"基础版 / 官方"的那条做第一次试跑。
4. **改输入再运行**：改提示词、分辨率、帧数、seed；第一次不要改模型文件，先跑通默认。
5. **取产物**：看界面右侧的"队列 / 历史"，点开结果预览或下载；
   文件通常落在镜像的输出目录（见第五节），也可在界面直接下载。

**UI 的适用边界**：调参、看效果、临时试一条提示词。批量、定时任务、可复现对照不要靠手点。

---

## 五、API 路径怎么用（给自动化/对照实验的主线）

### 先分清两层 API（顺序不能反）

| 层 | 谁提供 | 端点形态 | 什么时候用 |
|---|---|---|---|
| 作者封装 API | 镜像作者 | `/api/workflow/generate`、`/api/workflow/result?prompt_id=...`、`/api/comfy/start`、`/api/gpu/info`、并发 `/api/concurrent/*` 等；基址是面板 6008 外网 8443 | **优先**。一步提交工作流名+参数、自动取结果、能做多机并发批量；不需要自己拼节点图 |
| 原生框架 API | ComfyUI | `POST /prompt`、`/history/<id>`、`/view`；基址是 127.0.0.1:6006 | 要做单变量 A/B、节点级控制、或作者 API 不够用时 |

判定动作：先看作者 API 说明里的章节标题和端点清单（`grep -oE '<h[1-4][^>]*>[^<]{2,80}</h[1-4]>'`），
确认有没有一步提交/取结果的封装。**有就先用封装的**；原生接口留作精细控制。

**读到端点不等于端点可用。** 想说"这套作者 API 能用"，必须实际调用只读端点验证：
`curl -s -m 10 http://127.0.0.1:<面板端口>/api/gpu/info`、`/api/comfy/status` 等，
看返回是不是合法 JSON / 有真实字段。写报告的证据等级 distinction：只读到文档 = 源码级；
实调返回 200 且有数据 = 实测级。两者不可混写。

### 原生接口提交格式（最容易踩的坑）

ComfyUI 的 `POST /prompt` **必须包一层 `{"prompt": <工作流对象>}`**：

```
POST http://127.0.0.1:<端口>/prompt
Content-Type: application/json

{"prompt": { ...工作流 JSON... }}
```

- 直接把工作流 JSON 当请求体 → 返回 `no_prompt`，并可能触发某个节点的 `KeyError: 'prompt'`。
  这是**请求格式错**，不是镜像故障、不是节点坏了。
- 工作流 JSON 里的 `_api_config` 之类的配置字段要去掉，只留节点图。

### 提交后的轮询与取产物

1. 提交成功拿 `prompt_id`。
2. 轮询 `GET /history/<prompt_id>` 看 `status`，`status=success` 再取文件。
3. 从 history 的 `outputs` 里拿文件名，去输出目录读取；
   或调 `GET /view?filename=<名字>&type=output` 直接取文件流。

### 写对照实验时的硬要求

- 同一 prompt、seed、分辨率、帧数、步数都要**显式锁住并记录**；
- 帧数在 ComfyUI 工作流里常由 ComfyMath 表达式算出来，要**改上游参数**而不是改显示值；
- 记录 `prompt_id` 与输出文件名，事后能追溯是哪次提交。

详见 `layered-method.md` L4（A/B 帧数对齐、同时间点截帧、no_prompt 归因）。

---

## 六、取产物的位置（UI 与 API 常不一致）

探测顺序：

```
find <镜像根> -maxdepth 3 -type d -iname 'output*'
ls -lt <输出目录> | head -20
```

- 界面下载的文件与 API 产物可能落在**不同子目录**（实测基础版落在 `output/` 根，
  加速版落在 `output/video/`）。
- 按 `prompt_id` 或提交时间对号入座，别按文件名猜。
- 产物通常落在**系统盘**，量大时要规划输出目录位置（见 `environment-recipe.md`「盘位与扩容」）。

---

## 七、写进项目报告时怎么记

使用指南属于"一次性入口信息"，按这个最小表记，别堆成流水账：

| 项 | 记什么 | 证据等级 |
|---|---|---|
| 说明文件 | 绝对路径 + 前几行内容摘要 | 实测 |
| 访问地址 | 面板 URL / 端口映射方式；不要写密码 | 实测 |
| UI 入口 | 端口 + 是否需代理映射 | 实测 |
| API 入口 | 端口 + `/system_stats` 是否 200 | 实测 |
| 预置工作流 | 目录 + 条数 + 一条基础版文件名 | 实测 |
| 输出目录 | 绝对路径 + 两版实际子目录差异 | 实测 |
|  unverified | 外网访问是否可用、并发能力、长任务稳定性 | 未核实 |

**不要把端口、面板域名、工作流名写进 skill**——它们是实例数值，换镜像就失效；
skill 只保留本节这套"怎么找到它们"的探测方法。

---

## 八、反面做法（写教程时不要这样）

- 不探测就写"打开 6006 端口即可"——端口是实测出来的，不是默认值。
- 把面板地址/域名硬编码进 skill 或通用文档——换实例全失效。
- 只写 UI 不写 API——无法做可复现对照，等于没沉淀。
- 把 `no_prompt` 当成镜像故障去排查节点——先检查请求体格式。
- 产物目录按文件名猜——按 `prompt_id` 或提交时间对号入座。
