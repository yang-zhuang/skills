# MiniMax-H3 模型家族区别对照（固有规格，可跨镜像复用）

## 目的与证据口径

回答「同一个 H3 为什么有十几个文件名、它们的区别是什么、什么时候用哪个」。
**固有规格进这里，实例数值不进。** 每台镜像实际选了哪几个文件、实测耗时多少，留项目报告。

证据等级标记：
- **官方卡片**：HuggingFace 模型卡的声明（不是本机实测）
- **本地实测**：在目标实例上读到的字节数 / safetensors header / 跑出的结果
- **交叉印证**：官方卡片与本地实测一致
- **未核实**：卡片之间有冲突，或本机未验证

主要来源（写结论时带链接，不要只写数字）：
- Comfy-Org 打包清单（含全部变体与目录结构）：`https://huggingface.co/Comfy-Org/MiniMax-H3`
- 原始模型：`https://huggingface.co/MiniMaxAI/MiniMax-H3`（33B 全模态，约 13B 参数在可缓存 AdaLN 分支，4–15 s 片段、带同步立体声）
- INT8 bundle 卡片（含逐文件字节数、来源 revision、SHA-256、许可）：`https://huggingface.co/cokeadrink/minimax-h3-int8-base-ref2va/raw/main/README.md`
- NVFP4 DiT 卡片（量化层配置、硬件要求）：`https://huggingface.co/rockerBOO/minimax-h3-nvfp4-convrot`
- Qwen3-VL NVFP4 编码器卡片（ConvRot 陷阱、16 GB 卡实测）：`https://huggingface.co/Momoking/Qwen3-VL-32B-Heretic-MiniMax-H3-NVFP4`
- Turbo LoRA 4/8/2-step 速度与质量基准：`https://huggingface.co/Pika-Labs/Pika-H3-Flex-Turbo-LoRA`、`https://huggingface.co/lightx2v/Minimax-h3-Turbo`
- GGUF 小卡组合与推荐参数：`https://huggingface.co/ChrisColeTech/minimax-h3-turbo-GGUF`

## 零、先看懂文件名：缩写对照（新手先读这章）

H3 的文件名看着长，其实是「**任务前缀 + 精度后缀**」两段拼出来的。例如
`minimax_h3_fl2va_pruned_int8_convrot` 拆开就是 H3 / 首尾帧任务 / 剪枝 / 8 位整型 / 旋转量化。
**先按这两段读，再去看具体档位**，否则容易把「任务不同」误当成「画质高低」，或反过来。

### 0.1 任务前缀（决定喂什么输入，两族不能互换）

| 缩写 | 全称 | 人话 | 什么时候遇到 |
|---|---|---|---|
| `fl2va` | **FL**2**VA** = First-and-Last-frame to Video+Audio（官方名 H3-Base-FL2VA，首尾帧模式） | 最多喂 2 张图：0 张 = 纯文字生视频；1 张 = 当首帧或尾帧；2 张 = 首尾帧之间补出一段视频。输出自带立体声 | 绝大多数文生/图生视频工作流，**本镜像就是这一族** |
| `ref2va` | **Ref**2**VA** = Reference to Video+Audio（官方名 H3-Base-Ref2VA，全模态参考模式） | 喂参考素材来定身份/风格/动作/声音：≤9 图、≤3 段视频、≤3 段音频（音频不能单独喂），混合最多 12 个文件 | 要锁定某个角色、风格或音色时 |
| `t2va` / `i2va` / `l2va` | Text / Image / Last-frame to Video+Audio | 纯文字、一张图当开头、一张图当结尾——这三种都算 FL2VA 家族的用法 | 提示词指南按输入类型这么叫；官方开源公告只列 FL2VA / Ref2VA 两个 checkpoint |

> 来源：MiniMax 官方开源公告 `https://www.minimax.io/news/minimax-h3-open-source`（中文版 `https://www.minimax.cn/news/minimax-h3-open-source`）。
> 记住 **VA = Video + Audio**：H3 默认就生成 32 kHz 立体声，不是先出无声视频再配音。

### 0.2 精度后缀（决定显存和画质，可替换）

| 缩写 | 全称 | 人话 | 怎么选 |
|---|---|---|---|
| `bf16` | bfloat16，16 位浮点 | 原始精度，画质天花板，也最吃显存 | 大卡、最终成片 |
| `fp16` / `fp32` | 16 / 32 位浮点 | VAE 常用 fp16；音频 VAE 和量化 scale 保 fp32 | 别为了省几十 MB 删 fp32 音频 VAE |
| `int8` | 8-bit integer，8 位整型 | 权重压成 8 位整数，大幅省显存 | 官方首选，前提是 PyTorch 支持 cu130 |
| `convrot` | ConvRot = 权重旋转变换（分组哈达玛旋转） | 量化前先把权重「摊平」再压，减少 INT8 常见的画面崩坏/闪烁 | 配套要反旋转；**别拿 ConvRot 文件直接再量化**，会不报错地生成与 prompt 无关的画面 |
| `pruned` | 剪枝 | 裁掉约 13B 可缓存的 AdaLN 分支权重，体积明显变小 | 48 GB 卡跑 H3 的现实选择 |
| `fp8_scaled` | FP8 + scale 缩放 | 8 位浮点量化，带缩放系数 | INT8 跑不了时的兜底 |
| `nvfp4` | NVIDIA FP4，4 位浮点 | 比 8 位更省，但 Blackwell（SM≥10）才原生；其他架构走模拟约慢 2.3× | 见第三章冲突条，别照卡片无脑推 |
| `awq` | Activation-aware Weight Quantization | 按激活重要性分配量化位数的算法，常与 nvfp4 搭配 | 本镜像文本编码器就是这档 |
| `gguf` / `Q4_K_M` | GGUF 量化格式 / 其中一档 4 位量化 | 12–16 GB 小卡方案 | 显存不够时的退路 |

### 0.3 组件与推理词（读 header、看工作流会撞到）

| 词 | 全称 | 人话 |
|---|---|---|
| `DiT` | Diffusion Transformer | 真正把噪声去噪成视频的骨干，显存大头 |
| `VAE` | Variational Autoencoder，变分自编码器 | 把 latent 还原成像素/声音；H3 分视频 VAE 和音频 VAE 两个 |
| `LoRA` | Low-Rank Adaptation，低秩适配 | 附加的小权重，不改主模型就能改行为；这里的 Turbo 就是拿它加速 |
| `Qwen3-VL-32B` | 文本编码器底座 | 把提示词/参考图翻译成 DiT 能懂的条件 |
| `AdaLN` | Adaptive Layer Normalization，自适应层归一化 | DiT 里的调制分支，约 13B 参数可缓存；`pruned` 剪的就是它 |
| `turbo` + `2/4/8-step` | Turbo LoRA 的步数档 | 把 ~40 步压到 2/4/8 步。**8-step 是质量档，不是最快档**；用 turbo 时 CFG 必须 1.0 |
| `CFG` | Classifier-Free Guidance | 提示词遵循强度；Turbo LoRA 场景固定 1.0 |
| `rank` / `alpha` / `scale` | LoRA 低秩矩阵宽度 / 缩放分子 / 实际系数 | `scale = alpha / rank`，本镜像 LoRA 是 8/128 = 0.0625，即叠加到主模型的强度 |
| `SM 8.6` / `SM≥10` | GPU 架构代号（Streaming Multiprocessor 版本） | 8.6 = Ampere（如 3090），≥10 = Blackwell；决定 FP4 有没有原生内核 |
| `offload` | 显存卸载 | 把放不下的模型层挪到内存/磁盘，能跑但会变慢 |

**证据口径**：`fl2va` / `ref2va` 的定义来自 MiniMax 官方公告（官方卡片级）；精度后缀的通俗解释综合官方打包清单与社区模型卡（如 `liblib.art` 的中文释义），本机只通过 safetensors header 验证了 `I8/U8` 成对、`F8_E4M3` 等**存储事实**，没有逐个验证社区对 convrot 数学细节的说法。所以上表是「用来判断怎么选」的操作性解释，不是底层实现证明。

## 一、先分清四个位置（搞混就会装错）

H3 一套能跑的最小组合是**四类文件各一个**，它们互不替代：

| 位置 | ComfyUI 目录 | 作用 | 换错会怎样 |
|---|---|---|---|
| DiT / 主模型 | `models/diffusion_models/` | 真正生成视频的扩散 Transformer | 画面主体、运动、显存大头 |
| 文本编码器 | `models/text_encoders/` | Qwen3-VL 把 prompt 变成条件 | 语义理解、提示词遵循度；ConvRot 文件若被错误再量化，**不报错但生成与提示词无关的画面** |
| 视频 VAE | `models/vae/` | latent → 视频帧 | 解码色偏/伪影 |
| 音频 VAE | `models/vae/` | latent → 立体声音频 | 无声或音频异常 |
| Turbo LoRA（可选） | `models/loras/` | 把 ~40 步压到 2/4/8 步 | 不装也能跑，但慢很多；装错步数配置会糊 |

`fl2va` = 首尾帧 → 视频+音频（0/1/2 张图，覆盖文生/首帧/尾帧/首尾帧）；`ref2va` = 参考图/视频/音频 → 视频+音频（≤9 图、≤3 视频、≤3 音频，混合≤12 个文件）。**两者不是量化差异，是输入模态差异**，不能互换。缩写全称见第零章。

## 二、DiT（主模型）变体对照

全部来自 `Comfy-Org/MiniMax-H3` 打包，原始 BF16 约 66 GB。

| 文件名后缀 | 精度/处理 | 体积 | 特点 | 什么时候用 |
|---|---|---|---|---|
| `fl2va_bf16` | 原始 BF16 | ~66 GB | 最高保真 | 大显存、要最终成片质量 |
| `fl2va_pruned_bf16` | 剪掉 AdaLN 调制分支后再存 BF16 | 显著变小 | 去掉约 13B 可缓存分支权重 | 想保 BF16 精度但装不下 66 GB |
| `fl2va_int8_convrot` | INT8 + ConvRot（Hadamard 旋转） | 中等 | tensorwise INT8，需运行时反旋转 | 显存中等、要优于 FP8 的稳定性 |
| `fl2va_pruned_int8_convrot` | 剪枝 + INT8 + ConvRot | **20,970,379,616 B（19.5 GiB）** | 本镜像用的就是这个 | **48 GB 卡跑 H3 的现实选择** |
| `fl2va_pruned_fp8_scaled` | 剪枝 + FP8 scaled | 约 20 GB 级 | FP8 路线，兼容性好 | 不支持 INT8/FP4 专用内核时 |
| `fl2va_pruned_nvfp4` | 剪枝 + NVFP4 | ~20 GB | MLP 走 NVFP4、QKV/AdaLN 走 FP8、敏感层保 BF16 | **Blackwell（SM≥10）**；其他卡走模拟会慢约 2.3× |
| `ref2va_*` 同系列 | 同上各档 | 同上 | 参考图/音/视频输入 | 需要参考素材时 |

**本地实测交叉印证**（本实例，2026-11-25）：`minimax_h3_fl2va_pruned_int8_convrot.safetensors` 字节数 20,970,379,616，与 bundle 卡片逐字节一致；safetensors header 932 个张量，dtype 分布 `F32:210 / BF16:220 / F16:102 / I8:200 / U8:200`——I8 与 U8 成对出现正是 INT8 ConvRot 的存储特征，敏感层保 F32/BF16。**精度不是单一 I8，是混合精度**，所以不能把文件名里的 int8 理解成"全模型 8 位"。

## 三、文本编码器变体对照

原始是 Qwen3-VL-32B 适配版，Apache-2.0；`comfy_quant` 元数据逐层标注格式。

| 变体 | 体积 | 硬件 | 坑 |
|---|---|---|---|
| `qwen3vl_32b_minimax_h3_bf16` | ~64 GB 级 | 大显存卡 | 装不下会大量 offload |
| `qwen3vl_32b_minimax_h3_int8_convrot` | 26.4 GB | 16 GB 卡需 offload | 旋转权重，别拿去直接再量化 |
| `qwen3vl_32b_minimax_h3_nvfp4_awq` | **15,687,142,551 B（14.6 GiB）** | 官方卡片写 Blackwell；有卡片称 cu130 + comfy_kitchen 在 Ada 上启用 FP4 内核，否则模拟慢 ~2.3× | 见下方冲突条 |

**本地实测交叉印证**：本实例该文件字节数与 bundle 卡片逐字节一致；header 2054 个张量，dtype 分布 `F32:351 / BF16:651 / F8_E4M3:350 / I8:1 / U8:701`——`embed_tokens` 保留 INT8（卡片也这么写），其余是 F8_E4M3 + 量化 scale，所以文件名虽叫 NVFP4，实际是**NVFP4/AWQ 混合精度**。

**⚠ 冲突（必须如实写）**：官方卡片称 NVFP4 需要 Blackwell（SM≥10），但本实例是 RTX 3090（SM 8.6），加载并生成成功（L4 两版均 `status=success`）。另一卡片给出的解释是「cu130 构建 + comfy_kitchen FP4 内核可在 Ada 启用，否则走模拟约慢 2.3 倍」。3090 属 Ampere 而非 Ada，本机**走的是原生内核还是模拟路径未核实**，不要据此宣称"NVFP4 在 3090 上原生可用"。

**⚠ ConvRot 再量化陷阱（官方卡片）**：把 INT8 ConvRot 权重反量化后直接转 NVFP4、不撤销 Hadamard 旋转，文件能加载能跑，但生成内容与 prompt 完全无关且无任何报错。表现是"画质不对"时，先怀疑旋转不匹配，别只怀疑量化档。

## 四、两个 VAE

| 文件 | 字节数（本地实测） | header dtype | 作用/判读 |
|---|---|---|---|
| `minimax_h3_video_vae_fp16.safetensors` | 5,207,808,496（4.85 GiB） | 562 张量，全 F16 | latent→视频帧；带 `latents_mean/std`、`vae_clip_length=17`、`vae_token_drop=3`、diffusers 0.32.2 配置。比参考用途的 INT8 video VAE 更大，48 GB 双模型同时驻留要 offload |
| `minimax_h3_audio_vae_fp32.safetensors` | 605,254,808（577 MiB） | 917 张量，全 F32 | latent→立体声；`sample_rate=32000`、`latent_channels=32`、`output_channel=2`。很小，没量化必要；H3 的音频是一等公民，不要为了省这点空间删掉 |

两者字节数与 bundle 卡片逐字节一致（交叉印证）。

## 五、Turbo LoRA 变体（决定步数与速度的其实是这里）

| 档 | 步数 | 速度/质量取向 | 卡片基准（H200，5 s 片段） |
|---|---|---|---|
| 不装 LoRA | ~40 步 | 基线、最慢 | ~500 s |
| 2-step（草稿） | 2 | 只用来试构图/运动 | ~25 s（~2.4×），5 个 prompt 里 1 个出现伪影 |
| 4-step | 4 | 生产速度档 | 40 s（1.5×） |
| **8-step（本镜像）** | 8 | **质量优先，不是最快** | 70 s（0.86×，比官方 6-step 的 60 s 还慢） |
| 官方适配器 | 6 | 对照基线 | 60 s |
| 8-step HD | 8 | 细节最高 | 质量优先档 |

**关键判读**：8-step LoRA 的价值是"少步数下保质量"，不是"比 4-step 更快"。镜像名里的"提速500"无法由这个文件解释——卡片明确写加速来自**减少评估次数**、单步耗时不变。用 turbo 时 CFG 必须 1.0，8-step 推荐 steps=8（上限 16）。

**本地实测元数据**（本实例）：`minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors` 字节数 1,956,193,000（1.82 GiB），624 张量（`F32:208 / BF16:416`）；header 写明 `training_rank=128`、`training_alpha=8`、`training_scale=0.0625`（=8/128）、`base_model=Comfy-Org/MiniMax-H3 的 bf16 DiT`、`source_format=Diffusers PEFT LoRA`、`qkv_fusion=block diagonal B; concat A; alpha multiplied by 3`、`swi_glu_mapping=[value;gate]→[gate;value]`。

**注意一个前提**：这个 LoRA 的 base 是 **bf16 DiT**，而本镜像实际用的是 **pruned INT8 ConvRot DiT**——跨量化档叠加能否保持卡片声明的 4/8 步收敛，本机只验证了"能跑完、有画面"，没做质量对标。另有第三方提供 rank≈21 的动态秩压缩版（311 MiB，保留 97.72% Frobenius 范数），可省显存/磁盘，但与本文件的等价性**未核实**。

## 六、选型的判定问题（按显存/用途，不写死路线）

1. **显存多少？** 先分清文件体积 / GPU 常驻 / 运行峰值这三个数（**见第八章**）：本镜像这套 5 文件体积 41.4 GiB，但 ComfyUI 默认 TE 换出后 GPU 常驻约 24.9 GiB、峰值估计 26.2–31.6 GiB（864×480/124 帧）——**别把 41.4 GiB 当显存需求**。48 GB 可直接跑；24 GB 需 TE 换出 + offload；16 GB 走 GGUF Q4_K_M + 64 GB 系统内存。
2. **要参考图/音/视频输入吗？** 要 → `ref2va` 系列；不要 → `fl2va`。
3. **最终成片还是草稿？** 草稿 → 2-step；量产 → 4-step；质量优先 → 8-step 并用更高步数。
4. **卡支持什么内核？** 支持 INT8 ConvRot → 用 pruned_int8；Blackwell → 可试 NVFP4；其他卡遇到 FP4 要预期模拟降速。
5. **文件是不是被改过？** 先 `stat -Lc %s` 与卡片字节数比、再读 header dtype 分布；名字相同不等于内容相同。

## 七、给一张镜像写"模型区别说明"的动作模板

1. 从目标工作流反查文件名（见 `model-recipe.md` 主路径）。
2. 对每个文件：`stat -Lc '%s %n' <软链>` 取真实字节数；读 safetensors header 的 `__metadata__` 与 dtype 分布（只读前若干字节，别载入权重）。
3. 用文件名 + 字节数去 HuggingFace 搜卡片；**字节数逐位一致才算同一文件**，只凭名字一样不下结论。
4. 卡片之间冲突时并列写，标未核实；本机跑通不等于卡片硬件要求失效，写清本机型号与路径未验证。
5. 输出按「是什么 / 和谁互补 / 什么时候用 / 用了会怎样 / 证据等级」五栏写，不写"最好/最强"这类无口径结论。

## 八、显存估算（用户最关心，但最容易写错口径）

### 8.1 先分清三个数字，别混着报

**为什么文件体积不等于显存**：ComfyUI 不会把四个模型一次性全放上 GPU——它按顺序加载、用完换出；同时显存里还有激活值、注意力中间量、VAE 解码缓冲。这些**和文件大小无关，只和分辨率、帧数、token 数有关**。所以同一套文件，短片小画布和 1344×768 全长片的峰值能差十几 GB。

| 数字 | 含义 | 本镜像这套（Comfy-Org 档） |
|---|---|---|
| 文件总体积 | 磁盘上文件大小之和（含 LoRA） | **41.4 GiB**：DiT 19.53 + TE 14.61 + 视频 VAE 4.85 + 音频 VAE 0.56 + LoRA 1.82 |
| GPU 常驻权重 | ComfyUI 默认编码完 prompt 后把 TE 换出，留在显存的 | **约 24.9 GiB** = DiT 19.53 + 视频 VAE 4.85 + 音频 VAE 0.56 |
| 运行峰值 | 常驻权重 + 采样/解码工作内存 | **26.2–31.6 GiB**（864×480 / 5 秒 / 124 帧，工作内存 1.3–6.7 GiB） |

> 写结论时**必须带尺寸和时长**：「这套要 30 GB 显存」是废话，「这套在 864×480/124 帧下峰值约 26–32 GiB，1344×768 全画布要另算」才可用。

### 8.2 各档显存账（864×480、5 秒 / 124 帧）

来源：`modelvram.com/zh/minimax-h3-vram-calculator`（文件大小为精确值，GB 即 GiB；工作内存区间取自 RTX PRO 6000 实测 discussion #59）。这是**计算器估计 + 少量实测**，不是所有配置都在本机跑过。

| 组合 | GPU 上权重 | 全部文件（不含 LoRA） | 峰值区间 | 大致能装的卡 |
|---|---|---|---|---|
| 全精度 BF16，全部放 GPU | 115.1 | 115.1 | 116.4–121.8 | 数据中心 / 多卡 |
| 全精度，TE 换出 | 67.1 | 115.1 | 68.4–73.8 | 多卡 / 96 GB |
| 完整 INT8 ConvRot（未剪枝）全放 | 62.4 | 62.4 | 63.7–69.1 | 96 GB |
| 剪枝 BF16 + INT8 编码器 | 42.9 | 68.2 | 44.2–49.6 | 48 GB 紧张 |
| **剪枝 INT8 + NVFP4 编码器（本镜像）** | **24.9** | **39.6** | **26.2–31.6** | **32/48 GB 舒服；24 GB 需换出 + offload** |
| 剪枝 FP8 + NVFP4 编码器 | 24.9 | 39.5 | 26.2–31.6 | 同上 |
| GGUF Q8_0 + NVFP4 编码器 + INT8 VAE | 23.3 | 37.9 | 24.6–30.0 | 32 GB |
| W4A8 + NVFP4 编码器 + INT8 VAE | 17.8 | 29.5 | 19.1–24.5 | 24 GB |
| GGUF Q4_K_M + GGUF Q4_K_M 编码器 + INT8 VAE | 16.8 | 27.5 | 18.1–23.5 | 24 GB；16 GB 走流式 |
| GGUF Q4_K_M，编码器放 CPU | 14.0 | 27.5 | 15.3–20.7 | 16 GB + 64 GB 内存 |
| GGUF Q2_K，编码器放 CPU | 9.4 | 23.0 | 10.7–16.1 | 8–12 GB + 32–64 GB 内存 |

**社区实测锚点**（口径各不相同，**不与上表逐行横比**）：
- RTX 4090 D 24 GB：1344×768 / 107 帧 / 20 步 / 在线 INT8，峰值约 **18 GB**（SGLang）
- RTX 5090 + 60 GB 内存：864×480 / 124 帧 / NVFP4 / 10 步，峰值 **26.9 GB**
- RTX 3060 12 GB：1280×720 / 163 帧 / int4 + offload，峰值 **10–11 GB**
- 8 GB + 32 GB 内存：Q2/Q3 量化能跑，5 秒片约 9–10 分钟

> 冲突处理：早前记过「16 GB 卡实测峰值约 9.9 GB」，那来自特定卡片的小画布配置，**与 864×480/124 帧不是同一口径**，上表按计算器列 15.3–23.5 GB，两者并列、标未核实。

### 8.3 除了模型大小，还跟什么相关（按影响排序）

**A. 输入规模——最直接**
- **分辨率**：社区称"最大的单一显存和速度杠杆"。0.3 MP 与 0.4 MP 差别明显，1344×768 全画布约 1 MP。864×480 / 5 秒对应 **14,985 个视频 token**。
- **帧数 / 时长**：token 数随帧数增长；帧数对齐 `17k+5`，所以"5 秒"实际是 124 帧（24 fps 下 5.17 秒）。
- **参考素材数量**：`ref2va` 最多 9 图 + 3 视频 + 3 音频（合计 12 个），比 `fl2va` 多一路视觉 token。
- **提示词长度与图片数量**：影响文本编码器侧的序列长度。

**B. 运行时策略——同样文件，峰值能差十几 GB**
- **文本编码器是否常驻 GPU**：ComfyUI 默认编码完就换出；不换出就是多 14.6 GiB。
- **VAE 解码尖峰**：解码阶段显存会明显冲高。分块解码（chunk 4–8 帧）可消除爆显存；H3 的 video VAE 内部已 tile，但 ComfyUI PR #15446（解码内存不再随片段长度增长）与 #15456（tiled 重试前先释放其他模型）说明旧版本在这里反复翻车。
- **offload / 流式加载**：装不下就放系统内存，能跑但每步搬运 9–10 GB（4090 实测），代价是慢。
- **attention 后端**：Sage Attention 提速约 2.3×，但与 BF16 对照的 PSNR 降到 23.51 dB；低显存 attention + chunk feedforward 节点能压低采样中间量。
- **CFG**：H3 要求 CFG=1.0（超过会直接 abort），等于省掉双份 batch。
- **采样步数与缓存节点**：步数主要影响时间；Block Cache 一类节点会缓存中间特征，占额外显存。
- **ComfyUI 启动参数**：`--reserve-vram`（给系统/浏览器留 0.5–1 GB）、`--cache-none`、`--disable-pinned-memory`、`--disable-smart-memory`、`--disable-async-offload`；动态 VRAM 开启时 `--lowvram` 基本无效。

**C. 系统侧——最容易被忽略**
- **系统内存**：16/24 GB 卡建议 **64 GB RAM**；SGLang 的 16 GiB 方案会 pin 约 116.7 GB 主机内存；32 GB 内存的 3090 会被 OOM kill（加 `--disable-pinned-memory` 解决）。
- **显存 + 内存总和最好 ≥ 模型体积**，否则卸载时卡成幻灯片。
- **Windows 的 "shared GPU memory" 不是免费显存**，是拿系统内存顶，慢；页面文件放 NVMe。
- **同卡跑多个 ComfyUI / 浏览器 / 剪辑软件**会抢显存，并共享缓存目录时互相干扰。

### 8.4 怎么估、怎么实测

- **估**：先定尺寸与帧数 → 查 `modelvram.com` 计算器（选文件、TE 在 GPU 还是 CPU）→ 拿"峰值区间"上限留余量 → 再核对系统内存够不够装卸载下来的权重。
- **实测**：跑目标工作流时采峰值，而不是看生成前的空闲值：
  - `nvidia-smi --query-gpu=memory.used,memory.total --format=csv -l 1` 每秒采样，取采样 + 解码阶段最大值；
  - ComfyUI `GET /system_stats` 看 `vram_free`，生成前后各读一次；
  - 记录当时的分辨率、帧数、步数、TE 位置、启动参数——**缺一项这个峰值就不可复用**。
- **反面做法**：把文件总体积当显存需求；拿 864×480 的峰值去承诺 1344×768 能跑；不记尺寸和帧数就写"某模型要 X GB"。
