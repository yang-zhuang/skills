# Strata 提速与视觉开启实战（Qwen3.8-Flash-Next）

## 目的

沉淀 Strata（Niko1221/Strata）在 AutoDL 多租容器上**两类高频操作的实测路径**：

1. **把推理速度提上去**——默认配置在配额容器里会慢 3~4 倍，本文给出诊断与修复步骤；
2. **开启图片输入（vision）与加长上下文**——安装器装出来的默认是纯文本 + 128K，本文给出补装步骤。

所有数字均为单卡 RTX 3090 48G（vGPU）+ 12 核 CPU 配额 + 90G 内存配额实例上的实测值。
先读 `autodl-platform.md`（cpuset ≠ cpu.max、测速前预热），本文大量依赖那里的结论。

---

# 第一部分：提高推理速度

## 1. 先会测速，再谈提速（三个前置条件，缺一结论就是假的）

| 条件 | 为什么 | 违反的后果（实测） |
|---|---|---|
| **预热**：跑 3~5 次相同请求，取后几次 | MoE 专家缓存要十几轮才填满 | 冷启动 25 tok/s vs 热后 58，低估 40%+ |
| **换数据**：每次请求内容必须不同 | 同一条 prompt 反复路由同一批专家，缓存热集与负载"背题" | 复读测出 58，混合负载实际只有 31 |
| **交叉对照**：A→B→A→B | 顺序测试时"越跑越热"会被当成处理效应 | 得出过"绑核更慢"的错误结论 |

测什么指标：响应 JSON 的 `timings.predicted_per_second`（decode 速度），不看 `prompt_per_second`。

## 2. 最大的一开关：worker 池对齐 cpu.max（实测 3.7 倍）

**现象**：Strata 引擎默认按"宿主机全部物理核"铺 CPU 专家池（本例 47 个 worker），
而容器 `cpu.max` 只给 12 核时间片——47 个可运行线程在 12 核上互相踩踏，
专家供给跟不上，GPU 大量空转（利用率在 3~100% 间乱跳）。

**修复**：在 Strata 的 config（`strata-<model>.json`）的 `args` 数组里加：

```json
"--pool-workers", "11"
```

取值 = **cpu.max 的核数 − 1**（1 核留给 host loop，与引擎默认"物理核减一"的规则对齐）。

**实测对照**（12 条不同 prompt 的混合负载）：

| 配置 | tok/s | 说明 |
|---|---|---|
| 默认（47 workers） | 31–33 | GPU 利用率乱跳，瓶颈在 CPU 侧 |
| `--pool-workers 11` | **112–119** | p3/p4 两轮复现；草稿接受率不变，排除口径问题 |

注意：

- `--pool-workers` 是**引擎**（strata 二进制）的参数，不是 `serve/server.py` 的；
  server.py 通过 config 的 `"args"` 数组把它透传给引擎。
- 改完必须**重启服务**才生效（重启方法见 `autodl-ports.md` 的端口释放注意事项）。
- 这个教训是通用的：**任何按"宿主核数"自动铺线程池的软件，在配额容器里都要显式改小**。

## 3. 试过无效的路（别浪费时间）

- **`taskset` 绑 NUMA**：内存 99.9% 在 node0、线程 1/3 在 node1 的"错位"看起来很可疑，
  但全线程绑 node0 后差异仅 ~2 tok/s（噪声内），且绑单 socket 反而略慢（超线程兄弟核争用）。**保持默认。**
- **`--conversation-cache-mib`**：只影响多轮对话的 prompt 缓存，不提高单轮 decode 速度。
- **用"GPU 100%"判断瓶颈**：同 prompt 复读时 GPU 会钉在 100%（误判成 GPU 瓶颈、CPU 无关）；
  换混合负载后 GPU 掉到 3~35%——**判断瓶颈必须用混合负载**。

## 4. 硬件层的路径（按需）

- **换更低量化**（IQ3_XXS 等）：专家更小 → 显存缓存能装更多个 → 混合负载命中率更高，
  且每次未命中传输字节更少。在修好 worker 池之后收益会缩水，先做第 2 步再评估。
- **双卡**：仓库自测 IQ3_S 双卡 106 tok/s，代价是钱。
- **降 context**：context 越长 decode 越慢（仓库数据 1K→128K 约 -15%）；长对话场景才值得。

---

# 第二部分：开启图片输入（vision）

## 0. 选型：CPU 编码器 vs GPU 编码器

| | CPU 编码器（`gpu: false`） | GPU 编码器（`gpu: true`） |
|---|---|---|
| 显存 | **0**（mmproj 常驻内存 ~0.9 GB） | ~1.2 GB + `--vram-reserve-mib` |
| 速度影响 | 实测 decode 116.7 tok/s，与纯文本持平 | 快一些（编码阶段） |
| 适用 | 显存已被专家缓存吃满（实测场景） | 显存有余量 |

显存被 `--expert-cache auto` 吃满（实测只剩 458 MiB）时**只能选 CPU**。

## 1. 编译 strata-vision（安装器装纯文本时不会带这个二进制）

```bash
cd /root/code/Strata-main
export PATH=/root/code/Strata-main/.venv/bin:/root/miniconda3/bin:$PATH
cmake -S tools/vision -B build-vision -DCMAKE_BUILD_TYPE=Release \
      -DLLAMA_DIR=third_party/llama.cpp -DSTRATA_PORTABLE=OFF
cmake --build build-vision --target strata-vision -j 12
cp -f build-vision/bin/strata-vision engine/strata-vision
```

坑：产物在 **`build-vision/bin/`**，不在 build-vision 根目录；`-DSTRATA_PORTABLE=OFF` 是
setup.py 对本机编译的写法（默认 AVX2 基线是给别人分发的）。编译约几分钟，全程不联网。

## 2. 下载 mmproj 权重（866 MB BF16 视觉塔）

- 文件：`mmproj-Qwen3.8-Flash-Next-BF16.gguf`，属于 HuggingFace 的
  `ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF` 仓库，**ModelScope 有镜像**，国内直连即可。
- **远端没有 modelscope CLI/模块**（setup.py 是内置下载逻辑，不依赖 CLI）——直接 curl 直链：

```bash
curl -sL -o /root/autodl-tmp/strata-data/vision/mmproj-Qwen3.8-Flash-Next-BF16.gguf \
  https://www.modelscope.cn/models/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF/resolve/master/mmproj-Qwen3.8-Flash-Next-BF16.gguf
```

校验字节数 = **907,543,008**（与 HF 的 Content-Length 一致）。

## 3. 改 config（两处）

```python
# strata-<model>.json
# ① args 数组追加开关
args += ["--vision"]
# ② 加 vision 段（server.py 启动编码器的依据）
cfg["vision"] = {
    "exe":    "/root/code/Strata-main/engine/strata-vision",
    "mmproj": "/root/autodl-tmp/strata-data/vision/mmproj-Qwen3.8-Flash-Next-BF16.gguf",
    "model":  "<GGUF 第一个分片的路径>",   # server.py 用它喂给编码器
    "gpu":    False,
    "max_tokens": 300,     # 每张图最多折成多少 token（VISION cpu 默认 300）
    "threads": 11          # 必须手写！见下方坑
}
```

**坑**：setup.py 对 CPU 编码器默认 `threads = os.cpu_count() // 2`——容器里这是宿主机虚值（96//2=48），
又是"按宿主核数配线程"的坑。**手写成配额核数 − 1**。

只加 `"vision"` 段不加 `--vision` 时，新版 server.py 会自动补上开关，但显式写上更稳。

## 4. 重启并验证

```bash
kill <server.py 的 PID>        # 杀进程（注意别杀到外层 bash 包装）
# 确认 /proc/net/tcp 里 6006(0x1776) 监听数为 0 再启动
setsid nohup .venv/bin/python serve/server.py --engine strata --config strata-iq3_s.json --port 6006 > serve.log 2>&1 &
```

验证三件套：

1. `curl http://127.0.0.1:6006/health` → `"images": true`
2. `pgrep -af strata-vision` → 编码器进程带上了 `--mmproj ... --threads 11`
3. 端到端图片请求（OpenAI 格式，base64 data URI）：

```json
{"messages":[{"role":"user","content":[
  {"type":"text","text":"Describe this image."},
  {"type":"image_url","image_url":{"url":"data:image/png;base64,<...>"}}]}],"max_tokens":100}
```

实测用一张"黑底红方块+白字 RED SQUARE"的图，模型答：
"A red square is displayed on a black background beneath the white text 'RED SQUARE.'"（图内文字也能读）。

---

# 第三部分：加长上下文

- 模型原生训练长度就是 **262144**（`--yarn-orig-ctx` 的默认值）——`--max-context` 直接给 262144，
  **不需要** rope 缩放（`--rope-scaling`），无损质量。再往上才需要 yarn，代价是精度，不建议。
- 依赖 `--kv-resident N` 流式 KV：只有 N 个 cell 常驻显存，其余 KV 放 pinned **内存**——
  所以加长 context **只吃内存，不吃显存**。
- **内存账**：实测 128K→256K 后容器内存从 ~55 GB 涨到 **88/90 GB**（含 mmproj ~0.9 GB）。
  余量很薄；**OOM 的退路是把 `--max-context` 降回 192K/160K/128K 重启**。

---

# 第四部分：内置 Chat UI（开箱即用，零配置）

server.py 自己就是 Web 服务器，**根路径直接返回一个单页应用**，不需要任何额外安装/配置：

```
浏览器打开 http://127.0.0.1:6006/        （实例内）
        或 https://<随机串>.<区域>.seetacloud.com:8443/   （公网，经 6006 映射）
```

实测根路径返回 `<!doctype html> ... <title>Strata</title>`；页面是一个 SPA（`/monitor.html` 等
子路径不存在，各功能都在同一页内切换）。图标资源表显示的功能面：

| 功能 | 说明 |
|---|---|
| **Chat** | 对话主界面，含新建对话、历史 |
| **思考档位** | 菜单里直接选 off / low / medium / high（对应 API 的 `reasoning_effort`） |
| **附件（i-attach）** | 聊天里直接传图——视觉开启后即可用，无需写任何 API 调用 |
| **Monitor（i-activity）** | 实时显示模型状态与 GPU/CPU/内存占用（内存吃紧时在这里盯） |
| **About（i-info）** | 设置与地址信息 |
| 深色模式 / 导出 | 主题切换、对话下载 |

**⚠ 安全提醒**：`api_key: false` 时，**任何拿到公网地址的人都能打开同一个页面烧你的算力**。
要设 key 就在 config 里加 `"api_key": "<自定义密钥>"` 然后重启（设了 key 会同时关闭
DNS rebinding 检查，见 `autodl-ports.md`）；浏览器访问时页面会要求输入 key。

# 第五部分：思考模式与量化档位

## 思考模式（reasoning effort，四档）

请求加 `"reasoning_effort": "off|low|medium|high"`（或浏览器 Chat 菜单里选）。**实测对比**（同一问题 17×23）：

| 档位 | 行为 | 结果 |
|---|---|---|
| `off` | 完全跳过思考（`reasoning_content` 0 字） | 直接答 "391"，最快 |
| `high` | 先思考再答（思考内容单独放 `reasoning_content` 字段，与正文分离） | 同样答 "391" |

- **不传该字段时默认会思考**——跑基准测试时如果不关，思考 token 全算进生成速度里。
- 简单问答/批量任务用 `off` 省时间省 token；难题/数学/代码用 `high`。
- server.py 对思考有配套管理：`reasoning_budget_tokens` 预算、思考复读/死循环的检测与恢复。

## 量化档位全表（setup.py `MODELS`，均支持图片输入）

| 档位 | 下载 | RAM 要求 | 官方描述 | 同机速度比* |
|---|---|---|---|---|
| Q2_0 | 66.4 GB | 48 GB | "2-bit, the fastest"，质量最低 | ~1.8× |
| IQ2_XS | 68.0 GB | 48 GB | 2-bit i-quant，比 Q2_0 略好，速度接近 | ~1.5× |
| IQ3_XXS | 75.8 GB | 60 GB | 3-bit，质量比 2-bit 好 | ~1.17× |
| **IQ3_S**（常用） | 83.6 GB | 62 GB | **唯一 "matches the full model"**，最慢 | 1× |
| Coder（IQ1_M） | 58.4 GB | 32 GB | 512 专家砍到 256（留代码/工具/视觉用的），SWE-bench 91%，**代码之外含中文明显弱** | 最快 |
| UD-IQ4_XS | 93.7 GB | 48 GB | ~4-bit，质量高于 IQ3_S；<80GB 内存会从 SSD 读专家（慢） | 更慢 |
| Swift 1.5 | ~同档位 | - | 微调版：思考时间大幅缩短，质量基本不掉（无 IQ3_S） | 回答更快 |

\* 比值来自仓库同一台机器（RTX 5070）实测：Q2_0 94 / IQ2_XS 79 / IQ3_XXS 62 / IQ3_S 53 tok/s。

换档实操：重下对应 GGUF（ModelScope 镜像直链）→ setup 重跑或手工重建 pack（`tools/iq_pack.py`）→
config 的 `--native/--ple-gguf/--pack` 指向新分片 → 重启。**mmproj 不用重下**（视觉塔是通用文件）。
降档附带好处：arena 占用更小（IQ3_XXS 比 IQ3_S 省 ~8 GB 内存），256K 上下文的内存压力更小。

# 附录：排错速查

| 症状 | 原因与解法 |
|---|---|
| 编译完 `cp: cannot stat 'build-vision/strata-vision'` | 产物在 `build-vision/bin/` 下，find 后再 cp |
| `modelscope: No such file or directory` / `No module named modelscope` | 远端本没有 CLI；用 ModelScope 直链 curl 下载 |
| 改了 config 速度没变 | `--pool-workers` 要重启才生效；确认 `tr '\0' ' ' < /proc/<引擎PID>/cmdline` 里真的带上了 |
| 发图报 "this engine was started without --vision" | args 里没加 `--vision`，或改完没重启 |
| health 里 `images: false` | 同上 |
| 内存顶到 memory.max 后 OOM | 降 `--max-context`；改前备份 config（建议留 `.bak-<改动名>` 链） |
| 空返回 ≠ 没执行 | 通道卡死时命令可能已在远端成功（见 `shell-mcp-usage.md` 附录 D） |
