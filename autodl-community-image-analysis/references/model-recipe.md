# 模型配方：需要什么、放哪里、怎么下载

## 目的

回答三个问题：① 目标工作流到底引用哪些模型；② 它们实际落盘在哪、是不是软链；③ 该走「界面下载 / 手动下载 / 公共库软链」哪条路。
这是**探测动作模板**，不是「所有模型都要下载」的清单。整台镜像可能有上千个软链，还原只需补目标工作流实际引用的那几个。

## 判定动作与本次实测

| 问题 | 探测命令（原样可复制） | 本次实测（仅该实例该时刻） | 判定 / 动作 |
|---|---|---|---|
| 工作流引用了什么 | `grep -ohE '"[^"]+\.(safetensors\|gguf\|pt\|ckpt)"' /root/zealman-app/workflows/API-U03-minimax_h3_light2v-文生视频加速版API-V2.json \| sort -u` | 5 个：`minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16`、`minimax_h3_fl2va_pruned_int8_convrot`、`minimax_h3_audio_vae_fp32`、`minimax_h3_video_vae_fp16`、`qwen3vl_32b_minimax_h3_nvfp4_awq` | 从**工作流 JSON 反查**，不是列目录；列目录会得到 1530 个软链，无法指导还原 |
| 实际落盘路径与软链目标 | `find /root/ComfyUI/models -name 'minimax_h3_fl2va_pruned_int8_convrot.safetensors' -printf '%p -> %l\n'`（其余四个同理换文件名） | 主模型 / 两个 VAE / text encoder 都软链到 `/.autodl/Comfy-Org/MiniMax-H3/...`；Turbo LoRA 软链到 `/.autodl/34/96/d1/3496d1ffa7bedbbb2513f541b94dfc27` | 官方库文件可软链复用；社区 LoRA 是匿名 hash 路径，**凭文件名找不到**，要从镜像内取值或单独下载 |
| 模型规模 | `find /root/ComfyUI/models -maxdepth 3 -type l \| wc -l`；`find /root/ComfyUI/models/loras/minimax -maxdepth 1 -type l \| wc -l` | 1530 个软链；其中 minimax LoRA 114 个 | 不要把「下载全部模型」当还原步骤；只补目标工作流的 5 个 |
| 公共库是否可写 | `mount \| grep -i autodl` | `/.autodl-model/data` 为 AutoFS `ro` 只读 | **公共库软链优先**：在 AutoDL 上建软链不花流量、不占系统盘；尝试写入会失败 |
| 下载说明在哪 | `head -n 45 /root/ComfyUI/custom_nodes/comfyui-minimax-h3-audio-T8/docs/MODEL_DOWNLOADS_1.85.md` | 给出 HuggingFace 地址 `t8star/Taeh3-Comfy`、`t8star/Meridian`，精确到文件名、字节数、许可、源码 commit | 节点自带文档优先于镜像展示名；下载前先读节点 `docs/`，里面常写「只下其中一个」「不要叠加同一份 DMD」等关键约束 |
| 镜像自带资源入口 | `grep -inE '网盘\|huggingface\|模型' /root/zealman镜像说明.md \| head -n 20` | 镜像说明给出夸克网盘链接，并写明「支持 AutoDL 官方模型库软链接挂载，常用模型日更」 | 网盘链接通常是资源包/工作流，不等于权重本体；判定用途后再下载 |
| 界面下载器是否存在 | `find /root/ComfyUI/custom_nodes -maxdepth 3 -type f -iname '*download*' \| head -n 20` | 存在 ComfyUI-Manager、nunchaku `download_models.py`、audio-T8 下载脚本等 | 小权重/单文件可走 ComfyUI 界面或节点下载器；**大主模型优先公共库软链或手动下载**，别在界面里等几十 GB |

## 同一问题的多条探测路（主路径优先，备选看条件）

主路径没结果不等于「来源不存在」，先试备选。这些是**取用路径**，不是必查清单。

| 想知道 | 主路径（本次用的） | 备选路径 | 什么时候走备选 |
|---|---|---|---|
| 工作流引用了什么模型 | `grep -ohE '"[^"]+\.(safetensors\|gguf\|pt\|ckpt)"' <API 工作流>.json \| sort -u` | UI 工作流节点的 `widgets_values`；API `/object_info` 里对应节点的模型枚举；运行日志里的模型加载行 | 只有 UI 版工作流、没有 API 版时 |
| 模型是否真在盘且可读 | `find ... -printf '%p -> %l\n'` | `ls -l` 看软链目标；`stat -Lc '%s %n' <链接>` 看目标实际大小；ComfyUI 界面模型下拉框能否列出 | 要确认软链目标**可读**，不只是链接存在时 |
| 模型下载地址 | 节点 `docs/` 里的精确地址 | HuggingFace 模型页 README；`curl -I <url>` 看可达性；AutoDL 公共库文件列表 | 节点文档缺失、地址失效，或要确认许可时 |
| 模型该放哪个目录 | 看实际落盘目录 | `cat /root/ComfyUI/extra_model_paths.yaml`；节点代码里的 `folder_paths` 注册；工作流节点的模型类型 | 同一模型多处有副本，需确定工作流实际读哪个 |
| 文件是否完整 | 节点文档给的字节数 | `sha256sum <文件>` 与文档比对；`stat -Lc %s` 看实际字节 | 下载中断可疑、或节点文档明确给出校验值时 |
| 同名文件到底是哪一档 | 文件名里的量化后缀 | 官方卡片字节数 + safetensors header 的 dtype 分布 + `__metadata__`；两者逐位一致才算同一文件 | 名字相同但不能确认内容；要写"模型区别说明"时必做，详见 `minimax-h3-model-variants.md` |
| 这套要多少显存 | 计算器按文件 + 尺寸给的峰值区间（`modelvram.com` 等） | 跑目标工作流时 `nvidia-smi --query-gpu=memory.used,memory.total --format=csv -l 1` 采最大；ComfyUI `/system_stats` 读 `vram_free` | 要写"某档显存需求"、或卡片数字与本机/别的卡片冲突时；必须同时记分辨率、帧数、步数、TE 位置、启动参数 |

## 写「模型区别说明」的强制步骤（用户明确要求：不写清楚别人不知道怎么用）

**什么时候触发**：用户问「这些模型有什么区别 / 怎么用 / 量化版本差异」，或镜像里有多个同名不同后缀的检查点。

**五个字段缺一不可**，每个模型逐条写：
1. **是什么**：位置（DiT / TE / VAE / LoRA）+ 量化档 + 真实字节数。
2. **和谁互补**：哪几个文件必须配套；的同名变体属于"输入模态差异"还是"精度差异"。
3. **什么时候用 / 什么时候换**：按显存、卡型、草稿还是成片给判定问题，不写"最强/最好"。
4. **用了会怎样**：速度/画质/显存的代价，写清口径（谁测的、什么硬件、什么参数）。
5. **证据等级**：官方卡片 / 本地实测 / 交叉印证 / 未核实。**卡片之间冲突时并列写，不替它选边。**

**取证动作**：
- 读真实字节数：`stat -Lc '%s %n' <软链路径>`（软链要 `-L` 跟随）。
- 读量化元数据（只读前若干字节，别载入权重）：用 Python `struct.unpack('<Q', f.read(8))[0]` 取 header 长度 → `json.loads(f.read(n))` → 看 `__metadata__` 与张量 dtype 计数。
- 用「文件名 + 字节数」搜 HuggingFace 卡片；**逐字节一致才认作同一文件**，只凭名字相同不下结论。
- **要确认"同名文件真是同一份"必须 `sha256sum <文件>`（软链用 `stat -L` 跟随到实体再算），
  字节数一致只是初筛、不算同一文件**。大文件哈希耗时，后台跑并落日志，别卡住会话。
- 卡片声称的硬件要求与本机实测冲突时（例：NVFP4 要求 Blackwell 但 3090 跑通），写清本机型号并标「原生内核还是模拟路径未核实」，不宣称卡片失效。

**反面做法**：只列一张"文件名—大小"表就叫区别说明；把 8-step 写成"最快"（官方基准里它是质量档、比 6-step 慢）；把"文件名含 int8"说成全模型 8 位（实测是混合精度）；把官方卡片的基准数字当成这台机器的实测。

## 怎么选下载方式（判定问题，不写死路线）

1. **文件是否已在本机软链指向公共库？** 是 → 直接复用软链，不下载。用 `find ... -printf '%p -> %l\n'` 看目标。
2. **节点文档是否给了精确地址与字节数？** 是 → 走手动下载（`wget`/`curl`），下载后 `sha256sum <文件>` 与文档比对。
3. **只是单个小权重、节点自带下载按钮？** 是 → 打开 ComfyUI 界面用 Manager 或节点内下载器，省去手找 URL。
4. **以上都没有？** → 标「来源未核实」，不要猜 HuggingFace 仓库名。

## 放置目录（按工作流节点类型，不要全塞一个文件夹）

| 类型 | 实测目录 | 本次对应文件 |
|---|---|---|
| 主模型 | `/root/ComfyUI/models/diffusion_models/minimax/` | `minimax_h3_fl2va_pruned_int8_convrot.safetensors` |
| VAE | `/root/ComfyUI/models/vae/` | `minimax_h3_audio_vae_fp32.safetensors`、`minimax_h3_video_vae_fp16.safetensors` |
| 文本编码器 | `/root/ComfyUI/models/text_encoders/` | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` |
| LoRA | `/root/ComfyUI/models/loras/minimax/` | `minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors` |

**收尾校验**：五个文件都能 `test -e` 通过，再跑一次 `/system_stats` 与连通工作流；模型存在不等于工作流能跑，节点缺失会在提交时才报错。
