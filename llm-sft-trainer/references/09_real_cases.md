# 09 · 真实案例：正例与反例（都可复核）

这里的每一条都是**实际踩过并记录下来的**，不是编的教学例子。时间、路径、数字都能对上。

格式统一：**场景 → 反例（现象 + 证据）→ 正例（做法）→ 怎么提前发现**。

## 先看这次实跑的基线（可用来对照你的环境）

| 项 | 实测值 |
|---|---|
| 平台 | WSL2 Ubuntu / RTX 5060 Laptop **8151 MiB** / 驱动 592.01 |
| 环境 | 项目独立 venv：torch **2.15.0+cu132** / transformers **5.23.0** / trl **1.18.0** / peft **0.21.3** |
| 模型 | `Qwen3.5-2B`：总 **2.213 B**（视觉塔 0.331 B），文本塔 1.882 B |
| LoRA | r=16 / alpha=16，9 类模块，可训 **16.82 M（0.76%）** |
| 数据 | 80 条合成工单，seq 1024，batch 1 × GA 4 |
| LoRA bf16 | 峰值 **4566 MiB**，loss 0.683，20 步 **61.7 s** |
| QLoRA 4bit | 峰值 **3220 MiB**，loss 0.652，20 步 **73.9 s**（更省显存但慢 20%） |
| 探针输出 | `.eval()` 后正确；不加 `.eval()` 全是乱码（案例 10） |

这组数字的作用：2B 模型在 8 GB 卡上，**bf16 LoRA 也能跑，QLoRA 只是更宽裕**。
不要一上来就默认 4bit——先用 `probe_model.py` 估，再用这张表对照，最后 smoke test 实测。

---

## 案例 1：为了省下载，借别的环境的 torch（环境耦合）

**场景**：WSL 里已有 conda 环境 `vllm`（`torch 2.13.0+cu130` / `transformers 5.16.1`），
conda base 是空的（Python 3.13.5，torch/transformers 全 `MISSING`）。
新项目要装 torch，2~3 GB 下载太慢。

**反例**

```bash
python -m venv --system-site-packages .venv   # ❌
```

现象：`import torch` 能用，看起来"省了一次下载"。

证据与代价：

- 这个 `.venv` 的 torch 实际指向 `vllm` 环境；
- 升级/删除 `vllm`，本项目直接崩，报错路径还在另一个环境里；
- 两个项目共用一份 torch，版本需求冲突时互相拖累；
- `rm -rf .venv` 并不能"清理干净"，因为依赖根本不在里面。

**正例**

```bash
/usr/bin/python3 -m venv .venv        # 不继承任何已有环境
.venv/bin/pip install torch           # 自己装一份
```

**怎么提前发现**：问一句——删掉 `.venv` 后现有环境是否毫发无损？删掉任一现有 conda 环境，
本项目是否还能跑？两条都成立才叫隔离。

---

## 案例 2：把 4.3 GB 模型从 /mnt/d 复制到 ~/（白占磁盘，零收益）

**场景**：模型在 `D:\modelscope\Qwen3.5-2B`（WSL 下 `/mnt/d/modelscope/Qwen3.5-2B`），
听说"drvfs 比 ext4 慢 5~10 倍"，于是先复制到 `~/Code/sft-case/models/`。

**反例**

```bash
cp -r /mnt/d/modelscope/Qwen3.5-2B ~/Code/sft-case/models/
# real 1m10.599s   ≈ 62 MB/s
# 4.3G             白占 4.3 GB
```

收益要算清楚。实测同一模型的两种路径：

| 做法 | 第一次启动 | 第二次启动 | 磁盘 |
|---|---|---|---|
| 直接用 `/mnt/d` | **45.1 s** | 45 s | 0 |
| 先复制到 ext4 | 70 s + 加载 | 约 10~20 s | +4.3 GB |

**只跑一两次实验，复制纯亏；重启 3 次以上才开始回本。**

**正例**

```bash
python train_sft.py --model /mnt/d/modelscope/Qwen3.5-2B
```

**判断规则**（别靠感觉）：

```
复制耗时 < 重启次数 × (drvfs 加载耗时 − ext4 加载耗时)
```

达不到就别复制。真正该放 `~/` 的是：数据集（每 epoch 随机读）、输出（频繁写）、venv（大量小文件）。
详见 `07_platform.md` 第 3 节。

**怎么提前发现**：先问"这个文件读几次"。一次 → 原地用；每步/每 epoch 读 → 搬。

---

## 案例 3：多模态模型的参数不在 config 顶层（脚本直接 FATAL）

**场景**：`probe_model.py` 估算 `Qwen3.5-2B` 显存。

**反例**

```
FATAL: config.json 里找不到 hidden_size / num_hidden_layers，可能不是标准 decoder 模型
```

原因：`Qwen3.5-2B` 的 `config.json` 顶层只有 `architectures` / `text_config` / `vision_config`，
`hidden_size`、`num_hidden_layers` 全在 `text_config` 里。
凡是多模态模型（Qwen3-VL、Qwen3.5、InternVL、MiniCPM-V…）都这样。

**正例**：脚本按 `text_config` / `llm_config` / `language_config` / `decoder` 顺序回退，
并在输出里明确标注"视觉塔未计入"。修完后的真实输出：

```
## 结构
> 多模态模型（['Qwen3_5ForConditionalGeneration']）：结构取自 `text_config`，
  视觉塔参数未计入，参数量与显存估算偏低，必须以 smoke test 实测为准。

| hidden_size | 2048 |  num_layers | 24 |  vocab_size | 248320 |
## 参数量：1.67 B（bf16 权重约 3.1 GiB）
```

**而真值是 2.274 B**（直接读 safetensors header 算的）：

| 部分 | 参数量 |
|---|---|
| 文本塔 | 1.882 B |
| 视觉塔 | 0.331 B |
| 其它 | 0.061 B |
| **合计** | **2.274 B** |

估算偏低 27%。**这不是脚本的错，是"粗估"的定义**——但结论必须用实测复核，
所以 `04_train.md` 的 smoke test 不能跳。

**怎么提前发现**：看到 FATAL 别急着手工填参数，先看 `config.json` 有没有 `*_config` 子块。

---

## 案例 4：`target_modules` 写常见名字，只训了 4.52% 的参数（不报错）

**场景**：给 `Qwen3.5-2B` 配 LoRA。按通用教程写：

```python
target_modules=["q_proj", "k_proj", "v_proj", "o_proj"]   # ❌ 对多数模型没问题，对这个模型是坑
```

**反例的现象**：**不报错，正常训练，loss 也在降**——但效果就是不如预期。

证据（直接解析 `model.safetensors` 的 header，不依赖 torch）：

| 层类型 | 层数 | 注意力模块名 |
|---|---|---|
| `linear_attn`（线性注意力） | 18 | `in_proj_qkv` / `in_proj_z` / `in_proj_a` / `in_proj_b` / `out_proj` |
| `self_attn`（full attention） | 7 | `q_proj` / `k_proj` / `v_proj` / `o_proj` |

这个模型 24 层里只有每 4 层一次 full attention，其余是线性注意力。
所以 `q/k/v/o` 只命中 7 层（含 1 层 MTP），参数量对比：

| 写法 | 命中张量 | 覆盖参数 | 占全模型 |
|---|---|---|---|
| `["q_proj","k_proj","v_proj","o_proj"]` | 28 | 102.8 M | **4.52%** |
| regex（含 `in_proj_*`/`out_proj`/MLP） | 193 | 1425.1 M | **62.67%** |
| `all-linear` 近似 | 292 | 1760.7 M | 77.42% |

**正例**

```python
target_modules=r".*?(?:q_proj|k_proj|v_proj|o_proj|in_proj_qkv|in_proj_z|in_proj_a|in_proj_b|out_proj|gate_proj|up_proj|down_proj)"
```

注意 `all-linear` 会把**视觉塔**也带上（77.42% 里含 0.33 B 视觉参数），
纯文本 SFT 时不一定想要——这正是"先看模型结构再选 target"的原因。

**怎么提前发现**（不装 torch 就能查）：

```bash
python - <<'PY'
import json, struct
p="model.safetensors-00001-of-00001.safetensors"
with open(p,"rb") as f:
    n=struct.unpack("<Q", f.read(8))[0]; hdr=json.loads(f.read(n))
print([k for k in hdr if "in_proj_qkv" in k][:3])
print([k for k in hdr if "q_proj" in k][:3])
PY
```

或者在训练前打印 `trainer.model.named_parameters()` 里 `requires_grad=True` 的数量与名字，
和总参数量对比——**这个检查必须写进 smoke test**。

---

## 案例 5：纯文本环境跑多模态模型，先炸在 processor 上

**场景**：环境按"文本 SFT"配好（torch + transformers + trl + peft），
加载 `Qwen3.5-2B`（多模态）时，模型还没开始加载就报错。

**反例**

```python
processor = AutoProcessor.from_pretrained(model_path)
```

```
ImportError:
Qwen2VLImageProcessor requires the Torchvision library but it was not found in your environment.
Qwen2VLImageProcessor requires the PIL library but it was not found in your environment.
You can install it with pip: `pip install pillow`
```

原因：纯文本训练根本不需要 `torchvision`，所以 trl 的依赖里没有它；
但多模态模型的 processor 要处理图像，必须要有 `torchvision` + `Pillow`。

**正例**

```bash
.venv/bin/pip install pillow torchvision
```

装之前先 dry-run，确认不会把 torch 换掉（torchvision 版本必须和 torch 匹配）：

```bash
.venv/bin/pip install --dry-run pillow torchvision | tail -3
# Would install pillow-13.0.0 torchvision-0.29.2     ← 没有 torch，安全
```

**怎么提前发现**：看 `config.json` 有没有 `vision_config`、`architectures` 是不是
`*ForConditionalGeneration`。是的话，装环境时就把 `pillow torchvision` 带上。
这也是"先 probe_model 再装环境"的价值：结构信息能提前暴露依赖。

---

## 案例 6：空间到底被谁吃了（6 GB 在项目里，45 GB 在缓存里）

**场景**：装完训练环境，担心磁盘。

**实测（2026-11-15，WSL ext4）**

| 东西 | 位置 | 体积 | 能否删 |
|---|---|---|---|
| torch + nvidia CUDA 运行库 | `<项目>/.venv/.../site-packages/nvidia/` | **3.0 GB** | 随 `.venv` 一起删 |
| 整个项目环境 | `<项目>/.venv/` | **6.0 GB** | `rm -rf .venv`，删完不留痕 |
| pip 下载缓存 | `~/.cache/pip/` | **45 GB** | `pip cache purge` |
| conda 环境（本项目未使用） | `~/miniconda3/` | 21 GB | 未改动 |

**反例**：以为"装 CUDA 会污染系统"，于是忍着不装 / 去借别人的环境（见案例 1）。

**正例**：CUDA 运行库是 wheel（`nvidia-cudnn-cu13` 等）装进 venv 的，
`/usr/local/cuda` 与系统驱动都没被碰。清理顺序：

```bash
rm -rf ~/Code/sft-case/.venv      # 回收 6 GB，项目级
~/.cache/pip/purge 2>/dev/null    # 或 pip cache purge，回收几十 GB
```

**怎么提前发现**：`du -sh .venv ~/.cache/pip ~/miniconda3` 三条命令就看清了。
缓存往往比环境大一个数量级。

---

## 案例 7：WSL 系统 Python 缺 `Python.h`，triton 让训练起不来

**场景**：用 `/usr/bin/python3` 建了独立 venv，torch 能用（`matmul ok`），
但一开训练就崩，四个用例全是同一个错。

**反例现象**

```
subprocess.CalledProcessError: Command '['/usr/bin/gcc',
  '.../triton/backends/nvidia/driver.c', '-O3', '-shared', '-fPIC',
  '-o', '/tmp/tmpXXXX/cuda_utils.cpython-312-x86_64-linux-gnu.so',
  '-l:libcuda.so.1', ...]' returned non-zero exit status 1
```

坑在于 triton 编译时 `stdout=subprocess.DEVNULL`，**真正的错误被吞了**，
你只看到"gcc 返回 1"，完全不知道缺什么。手动编译才看见：

```bash
gcc -shared -fPIC -I/usr/include/python3.12 t.c -o t.so
# t.c:1:10: fatal error: Python.h: No such file or directory
```

根因：Ubuntu 的 `/usr/bin/python3` 只有运行时，没有 `python3-dev` 头文件；
triton 编译自己的 CUDA 辅助模块时需要 `Python.h`。

**正例 A（有 sudo）**

```bash
sudo apt install python3-dev
```

**正例 B（无 sudo，本项目实测可行）**

```bash
cd ~/pkg
apt-get download libpython3.12-dev        # 注意是 libpython3.12-dev，不是 python3.12-dev
dpkg-deb -x libpython3.12-dev_*.deb ~/local
export CPATH="$HOME/local/usr/include/python3.12:$HOME/local/usr/include"
```

之后 triton 正常：

```
triton 3.9.0 ok True
```

**正例 C**：用 conda 的 Python 建 venv（conda 自带 include/python3.x 头文件），
但要把 venv 建在项目目录下，别用 `--system-site-packages`（见案例 1）。

**怎么提前发现**：

```bash
ls /usr/include/python3.*/Python.h    # 不存在就先补，别等训练时才炸
```

顺便记住：这类错误的报错栈会指向 triton/gcc，看起来像"显卡或 CUDA 坏了"，
实际只是缺一个头文件。**看到 gcc 返回 1，先手动跑一遍那条 gcc 命令。**

---

## 案例 8：`AutoModelForCausalLM` 能加载，但静默丢了视觉塔

**场景**：同一个 `Qwen3.5-2B`，用两种方式加载，都"成功"。

**反例（不报错，但模型退化了）**

```python
from transformers import AutoModelForCausalLM
model = AutoModelForCausalLM.from_pretrained(path)
```

实测结果：

| 加载方式 | 实际类 | 参数量 | 视觉塔 |
|---|---|---|---|
| `AutoModelForCausalLM` | `Qwen3_5ForCausalLM` | **1.881 B** | ❌ 丢了 |
| `AutoModelForImageTextToText` | `Qwen3_5ForConditionalGeneration` | **2.213 B** | ✅ 在 |

差的那 0.33 B 就是视觉塔。训练照样能跑、loss 照样降，**只有到你 merge / 部署时才发现图像输入没了**。

**正例**

```python
from transformers import AutoModelForImageTextToText
model = AutoModelForImageTextToText.from_pretrained(path, dtype=torch.bfloat16)
```

**怎么提前发现**：加载完立刻打印

```python
print(type(model).__name__, sum(p.numel() for p in model.parameters()))
```

再和 safetensors 实际参数量对比（本机 2.274 B）。少了一大截，就是丢塔了。

---

## 案例 9：`assistant_only_loss` 不一定报错——先试再退

**场景**：想在 trl 里只对 assistant 回复算 loss。

我原先的判断是"Qwen 系模板没有 `{% generation %}` 标签，开了会报错"。
**实测推翻了**：trl 1.18 + Qwen3.5 的模板下，`assistant_only_loss=True` 正常训练。

同一份数据、同样 1 步：

| 配置 | loss |
|---|---|
| 全序列算 loss | 2.461 |
| `assistant_only_loss=True` | **0.282** |

loss 差一个量级，说明 mask 确实生效了（只算回复的几十个 token）。

**正例**：先开，报错再退

```python
SFTConfig(assistant_only_loss=True)
```

真报错时（模板确实没有 generation 标签），再退回：
- trl / unsloth：`train_on_responses_only`
- swift：`--loss_scale default`（SFT 默认就是只算 response）

**教训**：别凭"文档说模板不支持"就提前放弃一个更优的配置。
**先跑 1 步验证，再决定要不要绕**——这是本技能反复强调的"文档说的不算"。

---

## 案例 10：训练后忘了 `model.eval()`，生成全是乱码

**场景**：`Qwen3.5-2B` + LoRA，20 步训完，脚本直接 `model.generate()` 看效果。

**反例现象**

```python
trainer.train()
out = trainer.model.generate(**inputs, max_new_tokens=60)
```

输出：

```
{"status = 190/ / / / / / / / / / / / / / / / / / / / / ...
```

看起来像"模型训坏了"。而且极具误导性：

- 换 lr（2e-4 → 1e-4）：一样乱；
- 开/关 `assistant_only_loss`：一样乱；
- 换 `target_modules`：一样乱；
- loss 还降到了 **0.05**，怎么看都不像训崩。

**排除过程**（四步，见 `05_verify.md` 3.1）

| 步骤 | 结果 |
|---|---|
| 基座直接 generate | ✅ 正常 JSON |
| 基座 + 零初始化 LoRA | ✅ 正常 JSON |
| 训练后换新 `from_pretrained` 的 processor | ❌ 仍乱（排除模板被改） |
| 训练后在**训练样本自己**上 generate（加 `.eval()`） | ✅ 完全正确 |

**根因**：`trainer.train()` 结束后模型仍在 **train 模式**，手写 `generate()` 不会自动切换。
Qwen3.5 是混合线性注意力（24 层里 18 层 `linear_attn`），训练走分块并行实现、
推理走逐 token 递归实现，停在 train 模式生成会走错路径。

**正例**

```python
trainer.train()
trainer.model.eval()          # ← 这一行
out = trainer.model.generate(**inputs, max_new_tokens=60)
```

同一份权重、同一个 prompt、同样 20 步，只差一个 `.eval()`：

| | 输出 |
|---|---|
| 不加 `eval()` | `{"status = 190/ / / / / ...` |
| 加 `eval()` | `{"intent": "咨询", "order_id": "A1791", "urgency": "low"}` |

而且训练确实生效了：基座输出是带换行的 pretty JSON，训练后变成紧凑单行 JSON，
字段内容一致——**是风格被学会了，不是模型坏了**。

**怎么提前发现**：

- 任何"训完看效果"的脚本，`generate` 前先 `model.eval()`；
- `trainer.evaluate()` / `trainer.predict()` 内部会自动切 eval，手写循环不会；
- 现象是"固定乱码 + loss 很低"，优先怀疑评测代码，而不是重训。

---

## 案例 11：把两行真实日志拼成一行（"引用"变成了编造）

**这是我写 `10_measure.md` 的日志示例时犯的错，比写错参数名更严重。**

要展示 swift 的日志长什么样，我从 `docs/swift/BestPractices/Metax-support.md` 里摘了一行，
写成这样（**错误版本**）：

```python
{'loss': 1.40, 'grad_norm': 10.1, 'token_acc': 0.64,
 'global_step/max_steps': '85/94',
 'memory(GiB): 4.87', 'train_speed(iter/s)': 0.520}
```

看起来毫无问题。实际是**三处拼接**：

| 字段 | 我写的 | 第 137 行（85/94 步）的真实值 | 来源 |
|---|---|---|---|
| `loss` | 1.40 | 1.4406 | 第 137 行 |
| `token_acc` | 0.64 | 0.6267 | 第 137 行 |
| `memory(GiB)` | **4.87** | **6.5** | **第 120 行（1/94 步）** |
| `train_speed` | 0.520 | 0.520331 | 第 137 行 |
| 键名写法 | `'memory(GiB): 4.87'` | `'memory(GiB)': 6.5` | 引号位置错了 |

**为什么危险**：

1. 这条日志**在文档里根本不存在**。读者拿去和真日志对照，会以为"新版改了格式"，白查半天。
2. 它比凭空编造更难发现——每个字段都真实存在于文件里，只是不在一行。
3. `memory 4.87` 恰好是第 1 步的显存（模型刚加载，还没分配激活），
   `train_speed 0.520` 是训练稳定后的速度。**把这两者拼在一起，会让人误以为"训练全程只占 4.87 GiB"**——
   恰好制造了一个错误的显存结论。

**怎么发现的**：逐字段回原文 grep。`grep -n "4.87" ` 命中的是第 120 行，不是第 137 行。

**正确做法**：

- 引用日志/配置/参数时，**整行复制，不要手打**；
- 只摘字段时，注明"摘自第 N 行"，并保证所有字段来自同一行；
- 写完自检：把引用内容回原文 `grep` 一次，逐字段对。

**这条案例的普适性**：凡是"我看到了，我记下来"的环节，都可能发生这种拼装。
它不报错、不崩溃，只是让你的依据悄悄变成假的——和 `10_measure.md` 第三节说的"结论层变化"是同一类危险。

---

## 案例 12：用 1 步测速，把 6.7 小时估成 28 小时

**同一个脚本、同一份数据、同一台机器，只是训练的步数不同。**

| 跑了多少步 | `train_runtime` | 折算 s/step | 预测「1 万条 × 3 epoch」 |
|---|---|---|---|
| 1 步 | 13.45 s | 13.45 | 28.0 小时 |
| 2 步 | 13.50 s | 6.75 | 14.1 小时 |
| 20 步 | 64.07 s | **3.20** | **6.7 小时** |

（`out/result-fail-mask.json`、`result-probe2.json`、`result-lora-bf16.json`）

**高估 320%**。后果分两种，都很贵：

- 按 28 小时申请资源 → 多要 4 倍机器
- 按 28 小时判断"这个方案跑不起" → **否掉了一个其实能跑的方案**

**为什么**：第 1 步里塞了 cudnn benchmark、triton kernel 编译、显存池首次分配、dataloader 预热。
这些是一次性开销，摊到 20 步里只剩 0.6 秒，摊到 1 步里就是 10 秒。

**正确做法**：

```
测速 ≥10 步，丢掉前 2 步，取后面中位数
```

10 步以下的数字只能用来判断"会不会 OOM"，不能用来估时长。
详见 `10_measure.md` 1.2。

---

## 案例 13：同一张表，两把尺子（混用计时口径）

**写 `10_measure.md` 的口径说明时犯的，比案例 12 更隐蔽。**

同一个训练 run，结果 JSON 里其实有**两个**耗时字段：

| 字段 | 谁计的时 | 含什么 | 20 步的值 |
|---|---|---|---|
| `train_runtime` | Trainer 内部 | 只算训练循环 | 64.07 s |
| `train_seconds` | 我的脚本在外面掐 | 加上了建 trainer、存 ckpt、收尾 | 64.36 s |

我写表格时，**1 步那一行取了 `train_runtime`（13.45），20 步那一行取了 `train_seconds`（64.36）**，
于是算出 3.22 s/step、高估 318%。

统一到 `train_runtime` 之后：3.20 s/step、高估 **320%**。

**差 0.5%，两个数看起来都"对"**——这就是危险所在：

- 单看每一行，它都和源文件一致，没法说它是错的；
- 但整张表不成立，因为**前后两行不是同一个尺子量的**；
- 读者拿去复现，无论用哪个字段都对不上表里的数。

**怎么发现的**（这步才是重点）：写完不放心，写了个复算脚本，把文档里声称的 15 个数字
逐个回源 JSON 重算：

```python
checks = [('lora s/step 3.22', round(lora['train_seconds']/20,2), 3.22), ...]
# 15 项里 4 项 FAIL，全集中在含"步数折算"的那几项
```

**FAIL 一出现，先别看结论，先看"我是不是换了尺子"。**

**正确做法**：

1. **一张表只能有一个计时口径**，写在表头；
2. 优先用 `train_runtime`：它排除加载和存盘，只反映训练本身，也是三个库都会输出的字段；
3. **文档里的数字，自己先复算一遍再发**。写"可复算"的人不复算，等于没写。

**普适教训**：所有"比率、差值、百分比"都可能藏两把尺子——
分子用 A 口径、分母用 B 口径，结果既不等于 A 也不等于 B。
看到任何"高估 X%""快了 Y 倍"，先问：**分子分母是一个来源吗？**

---

## 案例 14：把两个正交的维度折成一个"成本"（用户指出的结构性错误）

**这是写度量文档第一版时最严重的错误，比案例 11、13 都严重——
那两个是数字错，这个是把问题本身搞错了。**

### 我写的（错误版本）

我把"显存占用"和"训练时长"折成一个叫"成本"的东西，排出这样一张表：

| # | 杠杆 | 省多少成本 | 代价 |
|---|---|---|---|
| 1 | 换更小的模型 | ÷3~4 | 掉 3~6 点精度 |
| 2 | 缩短序列 / packing | ÷2~3 | 基本不掉 |
| 4 | 换库 / fused kernel | ÷1.5~2 | 要重测 |

看起来很有条理。但这里面 **QLoRA 没法放进去**——
它省显存，却**增加时间**。在"成本"这一个轴上，它既是正贡献又是负贡献。

我当时怎么处理的？我把 QLoRA 从杠杆表里拿掉，另起一段说"它显存最小，但**又慢又贵**，
显存够的时候用它是纯亏"。

### 用户的原话

> "现在关注的节约显存，而不应是速度/时间。
> 如果要关注速度/时间的时候，可以通过其他方式去解决，
> 而不是交给节约显存的方法去解决，**这是两个不同的问题**。"

### 错在哪

**"成本"是个含混的词，它至少是三件事**：显存、时间、钱。
把它们加在一起，必须先给权重——而权重一给，结论就变成主观的。

更糟的是它导致**手段错配**：

| 我当时的隐含逻辑 | 后果 |
|---|---|
| "QLoRA 又慢又贵" | 判它劣选 → 撤掉它 → **显存立刻爆** |
| "显存和时间可以互换着算" | 以为省了显存就等于省了成本，于是容忍它慢，或者反过来 |
| 把两类手段排在一张表里 | 新手会拿"省显存的手段"去解"速度问题" |

### 正确写法

**两个正交维度，两张表，一次只优化一个：**

| 目标 | 用 | 代价 | 不要做 |
|---|---|---|---|
| 省显存 | QLoRA → ckpt → offload | **一定变慢** | 撤掉它来提速（会 OOM） |
| 省时间 | packing → 快 kernel | 多占显存 | 用省显存的手段（越搞越慢） |

QLoRA 的"慢"是**它买显存的价钱**，不是它的缺点。
只要慢的幅度还在时间档内，这个交易就该做，**做完就结束**。

### 连带发现的第二个错：把区间当成线

同一轮还发现：我把"可接受"写成了 `精度 ≥ 85%`、`时间 ≥ 5 小时` 这样的**线**。
线隐含"越往上越好"，于是 4.5 天和 5 天会被排出优劣，10 分钟和 15 分钟也会。

但人感知的是一档一档的跳变，**档内无差别**。
正确写法是**区间**：低于下限淘汰，高于上限浪费，**中间全部等价**。

### 怎么发现的

**不是自己发现的，是用户指出的。** 这说明一件事：

> 数字错，复算能抓到；**维度错，复算抓不到**——因为每个数字都是对的，
> 只是它们被放在了一起，算出了一个不该算的东西。

**唯一的防线是问自己**：这个指标，需要加权才能比较吗？
需要 → 那它其实是两个指标，先拆开。

### 检查清单（写任何"选型/对比"文档前过一遍）

- [ ] 我要排序的这个"成本/性价比"，是不是藏了两个以上的维度？
- [ ] 我给的每个手段，是不是都标了**它付出什么**（而不只是"省多少"）？
- [ ] 有没有哪个手段在这个轴上"既是正又是负"？——有，就说明轴错了
- [ ] 我说"可接受"时，给的是一条线还是一个区间？
- [ ] 我有没有把"够不着的选项"（多卡、换模型）写进表里，让读者以为自己漏了什么？

最后一条也是用户提醒的：**"要考虑现实实际情况，不要站在理想情况"**。
一张 8G 卡上的实习生，看到"多卡 ~N×"只会困惑，不会受益。

---

## 案例 15：让 Qwen3.8-27B 当实习生跑这个技能包（实测它的真实水平）

**这是唯一一条"用别的模型验证这套文档"的案例**，其它 14 条都是我自己踩的坑。

### 怎么测的

| 项 | 值 |
|---|---|
| 被测试者 | `Qwen3.8-27B`（AWQ-INT4，max_model_len 256k），通过 vLLM 提供 |
| 驱动方式 | dcode（Deep Agents Code，ACP 模式），**全新 session、独立进程** |
| 工作目录 | `D:/Code/skills/llm-sft-trainer` |
| 机器 | Windows 11 + WSL2，RTX 5060 Laptop 8151 MiB |

四次任务，总计约 19 分钟：

| # | 任务 | 耗时 | 结果 |
|---|---|---|---|
| 0 | 链路测试：列出目录 + 说出 SKILL.md 标题 | 135 s | ✅ 正确 |
| 1 | 给一份完整执行计划（8G 卡 / 2B 模型 / 5000 条数据） | 389 s | ✅ 七步、闸门、"区间不是线"全对 |
| 2 | 三个陷阱题（关 QLoRA 提速 / 3.2h→2.9h 值不值 / 两维一起抓） | 308 s | ✅ 三题全对 |
| 3 | **实际执行**第 2 步探环境，用真实输出汇报 | 333 s | ✅ 拿到真实硬件，主动进 WSL |

### 好消息：核心概念真的传到了

三题全对，而且有**超出文档的推理**：

- 问题 1 它区分了「显存够了」的两种语义：如果实测过 LoRA 装得下，那原错是"够却上 QLoRA"；
  如果只是"现在没 OOM"，换 LoRA 会 OOM（4566 vs 3220 MiB）→ **这正是文档想教的辨析**
- 问题 2 它自己推出："10% 在噪声里，他连'确实省了'都不确定，成就感建立在噪声上"
- 问题 3 它直接引用案例 14，说"两个正交维度折成一个效率"

**执行测试零幻觉**。它报的每个数字我都回机器核对了：

| 它说的 | 实际 | 对不对 |
|---|---|---|
| RTX 5060 Laptop GPU / 8151 MiB | `nvidia-smi`：8151 MiB，驱动 592.01 | ✅ |
| torch 2.13.0+cu130 | 同 | ✅ |
| transformers 5.16.1 / vllm 0.28.0 | 同 | ✅ |
| trl / unsloth / peft / bnb 全 MISSING | 同 | ✅ |
| 算力 12.0（Blackwell） | `get_device_capability()` = (12, 0) | ✅ |

它还主动做了两件我没要求的事：
1. **如实标注拿不到的项**："宿主侧 CUDA 信息拿不到，因为那边没装 torch，不是机器没有"
2. **主动指出前提**："这个结论前提是 7B；如果实际是 2B，LoRA 可能装得下，需要重算"

### 坏消息：入口不清楚，它把 30% 的步数花在找路

四次任务的工具调用，**重复率极高**：

| 现象 | 实测 |
|---|---|
| 反复列目录 | `ls` 4~6 次 + `glob` 1~3 次 + `dir` 1~3 次，才确认结构 |
| 路径写法试错 | `/D/Code`、`/d/Code`、`D:\Code`、`//?/D:/Code` —— **四种** |
| Read 读中文失败 | `Read` 连错 3 次 → `powershell Get-Content` → `.NET ReadAllText` → `python io.open` 才成功 |
| 给计划时过度阅读 | 读 **9 篇** references（00/01/02/03/04/05/07/12 + SKILL），389 秒 |

**关键判断：这不是"内容看不懂"，是"入口没写清楚"。**
它理解得非常好，但每次都要先花几分钟搞清楚"我在哪、文件怎么读"。

### 一个意外的正面信号

它读 `09_real_cases.md` 时，用的是**文档里教的渐进读法**：

```bash
sed -n '1,120p' 09_real_cases.md       # 先看开头
grep -n "14" 09_real_cases.md          # 定位案例 14
sed -n '572,650p' 09_real_cases.md     # 只读那一节
```

动作笨拙，但**方法是照做的**。说明"渐进读"这件事是可教的，不是只有人才会。

### 因此改了什么

`SKILL.md` 新增第 0 节「从这里开始（30 秒）」：

1. 根目录 = 当前工作目录，所有路径相对它
2. 不要反复 `ls`，直接看分层表
3. 不确定 cwd 就跑一次 `pwd`，不要猜四种写法
4. 读中文 md 失败时换读法（附命令），**不许跳过**
5. 一张"按你要做的事选读"表，防止给计划时把 9 篇全读

### 这条案例的普适结论

> **文档能被读懂，和文档能被用起来，是两件事。**
>
> 前者靠内容（概念、例子、反例），后者靠**入口**（我在哪、先读哪篇、读不到怎么办）。
> 前者做得再好，后者缺失时，使用者会把大量步骤花在找路上——看起来在努力，其实在原地。

**检查你自己的文档时，先问三个入口问题**：
- 读者知道自己的工作目录和文档根目录的关系吗？
- 读者知道这次任务**只需要**读哪几篇吗？
- 读者读不到内容时，有没有兜底路径？

---

## 案例 16：完整七步实测——第一轮 3 过 3 挂，改完后 7/7 全过

案例 15 测的是四个片段。这一条是**真跑完整流程**：同一个 session 连续七步，
真写文件、真跑命令、真训练（`scripts/selftest_e2e.py`）。

**这条案例的价值在"两轮"**：第一轮暴露了三个真问题，改完之后第二轮全过。
不是"一次就成功"的故事，是"测出来 → 改 → 再测"的闭环。

### 第一轮：前三步通过，第四步开始崩

| 步 | 耗时 | 调用 | 产出物 | 判定 |
|---|---|---|---|---|
| 1 定任务 | 350 s | 32 | `00_task_profile.md` 3555 B | ✅ 质量高 |
| 2 探环境 | 457 s | 28 | `01_env_report.md` 4719 B | ✅ 拿到真实 8151 MiB |
| 3 定方案 | 394 s | 18 | `02_training_plan.md` 11224 B | ✅ 三库对照完整 |
| 4 备数据 | 321 s | 29 | **缺失** | ❌ 钻进 label mask |
| 5 写脚本 | 514 s | 20 | **没有 train.sh** | ❌ 5 个 probe 脚本 |
| 6 训练 | 103 s | 3 | **缺失 outputs/** | ❌ 诚实放弃 |
| 7 验收 | >17 min | — | 无 adapter 可验 | ⚠️ 未完成 |

**前三步的质量真的高**，不是勉强及格：

- 第 1 步自己算出"24 条探针单条粒度 ≈ 4.2 个点，95% 以上就是无效投入"
- 第 2 步拿到 WSL 8151 MiB / Windows 7899 MiB 两个读数，**并解释了 252 MiB 的差**
- 第 2 步明确选 LoRA 不选 QLoRA，理由："省下的 3 GiB 是**不需要的**"
- 第 3 步写了 11 KB 方案，三库对照 + 为什么不用另外两条

**然后第 4 步开始，全崩。**

### 失败链：每一步单独看都很"严谨"

```
第 4 步：为确认 assistant_only_loss 到底怎么工作
        → 查 docs_outline（只有标题，没有正文）
        → 读 trl 源码 SFTConfig
        → 查 chat template 有没有 {% generation %} 标记
        → 写 qa_check.py / token_probe.py 验证
        → 29 次调用，没写 03_dataset_report.md

第 5 步：接着上一轮的疑问继续
        → load_probe.py / trl_patch_probe.py / trl_patch_probe2.py
        → trl_patch_probe3.py / gen_probe.py      ← 5 个 probe
        → 20 次调用，train.sh 一个字没写

第 6 步：一看没有 train.sh、没有 outputs/
        → 诚实地说 "step 5's script was never written"
        → 3 次调用就停了（没有编造训练结果）
```

**关键洞察**：第 6 步的"诚实"是对的，但它暴露了前两步的白干。
**每一步的验证动作单独看都合理，合起来产出为零。**

而且它自己说过一句话，正是死因：

> "Shell stdout is being swallowed. Let me use the grep tool directly on the virtual path."

**它读不到 bash 的输出**（Windows + dcode 的已知问题），所以每个验证都要
"写脚本 → 跑 → 把结果写到文件 → 读文件"，成本是正常环境的 3 倍。
读不到输出 → 更不确定 → 更加倍验证 → 死循环。

### 三个原因，两个能修

| 原因 | 能不能修 | 怎么修 |
|---|---|---|
| **没有探索预算**：没人告诉它"验 3 次就必须走" | ✅ 能 | `SKILL.md` 第 5 条硬约束 |
| **步骤边界不清**：label mask 的决策和验证混在一起 | ✅ 能 | `03_data.md` 加"该在这步做 / 留到第 5 步"表 |
| **bash 输出被吞**：它读不到命令结果 | ❌ 环境问题 | 只能记下来，换环境或换 agent 实现 |

### 改动（四条）

1. **`SKILL.md` 加第 5 条硬约束「探索有预算」**
   一个事实最多验 3 次，超了就按最保守选项决策，在产出物里标注"未验证｜假设 X｜风险 Y"，然后往下走。
2. **`03_data.md` 加分工表**：第 4 步只决定"开不开"，参数拼写和实际行为留到第 5 步，
   且**以跑通为准，不以读源码为准**。
3. **`04_train.md` 加第 0 节「先让它跑起来」**：`train.sh` 是这步唯一产出物；
   如果你在写第 3 个 `*_probe.py`，就去写脚本。
4. **`07_platform.md` 加第 8 节「从 Windows 调 WSL 的命令怎么传」**（第二轮才发现的）
   外加 `selftest_e2e.py` 第 5 步的产出物从"无"改成 `train.sh`——
   原来第 5 步没定义产出物，它写 5 个 probe 也算"过"，这是检查器的漏洞。

### 第二轮：7/7 全过

改完重跑。第 4 步第一次还是失败（230 s / 25 调用），但**失败原因换了**：

```
第一轮：钻进 label mask，读 trl 源码         ← 已修复
第二次：label mask 不钻了，直接跑 check_dataset，跑通了
        但卡在 heredoc 被 wsl 吃掉引号，报告没写出来   ← 新暴露的问题
```

补上 `07_platform.md` 第 8 节后，第三次通过：

| 步 | 耗时 | 调用 | 产出物 | 判定 |
|---|---|---|---|---|
| 4 备数据 | 730 s | 45 | `03_dataset_report.md` 3028 B | ✅ |
| 5 写脚本 | 1464 s | — | `train.sh` 1103 B + `train.py` 8448 B | ✅ |
| 6 训练 | 1091 s | 41 | `outputs/` **580 MB**（checkpoint + final） | ✅ |
| 7 验收 | 796 s | 30 | `05_eval_report.md` 7536 B | ✅ |

**七步全部有产出物，总计约 88 分钟**（1-3 步 20 min + 4 步 12 min + 5-7 步 56 min）。

**最有说服力的三个细节**：

1. **七步之间的信息真的传下去了**。第 5 步 `train.py` 的参数——
   `assistant_only_loss=True`、`packing=False`、`max_length=2048`——
   和第 3、4 步的决策逐条对上。它不是重新猜的。

2. **训练真的跑通了**。`outputs/full/checkpoint-20` 的 `trainer_state.json`：
   loss 从 0.202 降到 0.024，无 NaN，adapter 62 MB。

3. **第 7 步敢说"不达标"**。它算出 tuned 18/24 = 75%，低于第 1 步定的 22/24 下限，
   直接标 ❌ 并写"差 4 条"。同时用"24/24 条输出与 base 不同"证明 adapter 确实生效——
   **既没粉饰，也没因为不达标就怀疑自己**。

**代价要说清**：每步 8~24 分钟，其中很大一块是 dcode 在 Windows 下的工具开销
（读不到 bash 输出、反复裸 `ls`）。同样的任务，一个熟练的人大约 40 分钟能做完。

### 顺带证伪的一件事：关思考不能提速

实测同一任务（第 1 步），三种配置：

| 配置 | 耗时 | 调用 | 产出 |
|---|---|---|---|
| 直连（默认开思考） | 358 s | 33 | 4854 B |
| 直连 + 改 SKILL 路径约定 | 350 s | 32 | 3555 B |
| **加关思考代理**（`chat_template_kwargs.enable_thinking=false`） | **428 s** | 33 | 3861 B |

**关掉思考反而慢 70 秒**。原因：思考只占总时间一小部分，
而关掉之后工具选择变差、试错变多，净效果是负的。

**结论**：优化一个 agent 的执行效率，**先去砍无效工具调用，不要去砍推理**。

### 普适教训

> **严谨的局部动作，加起来可以是零产出。**
>
> 判断一个实习生（人或模型）行不行，不要看他每一步做得多细，
> 要看他**有没有在预定步数内交出产出物**。
>
> 所以流程文档必须写两样东西：
> 1. 每步的产出物（已有，就是七步表里的那一列）
> 2. **每步的预算**（这次才补上：验 3 次就必须走）
>
> 只写产出物不写预算，等于给了终点没给时限——认真的人会一直在路上。

### 但七步跑通，不等于闭环

7/7 有产出物，只证明「能顺着走完」。当时**第 7 步的结果是不达标的**（18/24 < 22/24，差 4 条），
而整个文档库里没有一条「不达标怎么办」的路径：

```bash
grep -rn "回到第\|重做第\|回退\|迭代.*第.*步" SKILL.md references/*.md
# → 0 命中（仅两处无关匹配：--budget 的分块回退、config 回退）
```

`00_workflow.md` 原文写的是：**"不通过就停在这一步，不许往后跳"**——
只有「停」，没有「退」。这套流程只能往前，不能往回。

结果是：跑完七步 → 结果不好 → 该干什么，文档不说。
**这就是流水线和闭环的差别，差别只在一条回边。**

补法是 `05_verify.md` 第六节那张表：诊断结论 → 回第几步 → 只改哪个变量 → 这是第几轮。

**检验任何流程文档是否闭环，问三个问题：**

| 问 | 不闭环的表现 |
|---|---|
| 每步的产出物，下一步真的在用吗？ | 第 7 步的标准不是第 1 步定的，各步自说自话 |
| 失败了回到哪？ | 只写"不通过就停"，没有反向箭头 |
| 会不会一直循环？ | 没写迭代预算，陷在调参里出不来 |

三个里缺一个，就是流水线，不是闭环。

---

## 案例 17：文档里嵌图片，把 agent 的会话打崩了

**这是我自己造成的**，而且只有"让 agent 真的跑一遍"才能发现。

### 现象

第 3 步跑了 **1037 秒，0 次工具调用，产出物缺失**。第 4 步紧接着 1 秒也失败。

`progress.jsonl` 里看起来像"模型不愿意干活"，实际是整个链路崩了：

```
RuntimeError: ACP error -32603: Internal error
```

### 根因

dcode 的 `media_utils.py:189` 生成的是 **Chat Completions** 格式：

```python
{"type": "image_url", "image_url": {"url": f"data:image/{fmt};base64,{b64}"}}
```

但它走的是 **Responses API**，转换后变成 `{"type": "input_image", ...}`，
**缺了必填的 `detail` 字段**，vLLM 直接 400：

```
openai.BadRequestError: 400
"ResponseInputImageParam detail Field required [type=missing]"
```

**验证方法**（1 分钟复现）：单独发一个"用 Read 打开 plan-wide.png"的请求 →
同样的 `-32603`，稳定复现。

### 为什么这一轮才炸

上一轮技能包里**一张图都没有**。这一轮为选型图新增了 `references/img/*.png`，
而第 3 步必读 `12_tradeoff.md`，里面有指向 `img/plan-wide.png` 的图片链接
——agent 顺着链接去读了图片。

**给 agent 用的文档里嵌图片，等于给自己埋雷。**

### 改动

| 改动 | 为什么 |
|---|---|
| 图片移出 `references/`，放 `assets/img/` | `references/` 是 agent 必读目录，`assets/` 不是 |
| `SKILL.md` 第 0 节加第 5 条禁令：不要 Read 图片 | 光移走不够，agent 也可能在别处碰到 |
| `12_tradeoff.md` 图片路径改 `../assets/img/` | 人看得到，agent 不顺着读 |

### 两条可复用的判断

**1. 给 agent 用的文档，图要和人分开。**

| 读者 | 放哪 | 怎么引用 |
|---|---|---|
| 人 | `assets/img/` | markdown 相对路径 |
| agent | 不要放图 | 让它**跑脚本生成**，而不是读现成的 |

图片对人是信息，对 agent 是**一段 base64 垃圾**，还可能触发工具链 bug。

**2. `0 次调用 + 超长耗时` 是链路故障，不是模型不动脑。**

看到这个组合要立刻查 `stderr`，不要去改 prompt。
一个模型再懒，也会先试着 `ls` 一下。**连续 0 次调用只可能是通道断了。**

`selftest_e2e.py` 已加判断：`calls == 0 且 seconds > 60` 直接标"链路故障"，
不当作步骤失败计入统计。

---

## 案例 18：换任务重跑一遍——5/7 步有产出物，但踩到三个新坑

案例 16 是同一个任务跑两轮。这次**把四个变量全换掉**再跑，才测得出"是不是只对某个场景成立"：

| 维度 | 案例 16 | 这次 |
|---|---|---|
| 任务 | JSON 字段抽取 | **客服回复生成**（自由文本，输出 70 token） |
| 数据格式 | `messages` | **`prompt`/`response`**（要自己转） |
| 模型 | Qwen3.5-2B（混合线性注意力） | **Qwen3-0.6B**（标准注意力，28 层） |
| 能力 | 2B，勉强能学 | **0.6B，学不全** |

结果：**训练真跑通了，但第 7 步的报告没写出来。**

| 步 | 耗时 | 调用 | 产出 |
|---|---|---|---|
| 1 定任务 | 846 s | 54 | ✅ 4438 B |
| 2 探环境 | 196 s | 9 | ✅ 4339 B |
| 3 定方案 | 800 s | 33 | ✅ 8139 B + `plan.json` + `plan.png` |
| 4 备数据 | 217 s | 10 | ✅ 5053 B |
| 5 写脚本 | 1464 s | — | ✅ `train.sh` + `train.py`（超时，但文件写完了） |
| 6 跑训练 | 1440 s | — | ✅ `outputs/full/`（adapter 40 MB，loss 1.341→0.146） |
| 7 验收 | 527 s | 38 | ❌ 跑了评测，报告没写 |

**第 5、6 步标"超时"但产出物都在**——超时只是单次请求的上限，不是任务失败。
所以 `selftest_e2e` 的统计要分开看两件事：**产出物在不在** vs **这一轮问完了没有**。

### 坑一：base vs tuned 的对比根本不公平

评测结果：base **0.0%**，tuned **58.3%**，下限 75% → 不达标。

第一反应是"判分脚本有 bug"。剥离 think 后重算，**结论完全不变**（0.0% / 58.3%）。
真正的原因是：

```
base ：真思考，每条输出 350+ 字，答案没写完就被 max_new_tokens 截断 → 0 分
tuned：学成了"空 think + 直接套模板"，42 字 → 过关
```

**这次训练学到的是"把思考关掉了"，不是"学会了任务"。** 提升里有一部分是格式适应。

教训：**base 和 tuned 必须用同一套 thinking 配置**，否则这个对比不成立。
详见 `05_verify.md` 2.1。

### 坑二：`check_dataset.py` 只认 3 种格式

agent 的原话：

> `check_dataset.py` 对 `prompt/response` 字段名不识别（只认 `prompt/completion`），
> 该轮数字是 raw JSON 文本的 token 数，只作方向参考。

它没有假装数字可信，但根子在我——常见格式漏了一个。已修：`prompt` 可配
`completion` / `response` / `answer` / `output`，另加 `query`/`response`。

**教训**：写"自动识别格式"的脚本，宁可多认几个同义字段名。认不出时的降级是
"按 raw JSON 长度统计"——这个数字看起来像真的，最危险。

### 坑三：84 KB 的 json 读不出来，527 秒全花在换引号

第 7 步的轨迹：4 次裸 `ls` → 读 `05_verify.md` → 找到 `eval_results.json` →
然后**连续十几次**尝试用不同的引号写法打印它（单引号、双引号、heredoc、cmd、写临时文件再 cat）。

它甚至自己在推理里写出了正解：

> Simplest, most robust approach: ... output to a file in /tmp inside WSL and then cat it

但没能执行完。**问题不是不会，是没在预算内停下来换通道。**

已加进 `SKILL.md` 第 0 节第 6 条：同一条命令换到第 3 种写法还读不到，
就停止调整命令，改成 `> out.txt` 落盘 + `Read` 工具读。

### 坑四：第 5/6 步"超时"，但产出物其实都在

`progress.jsonl` 的两行：

```json
{"step": 5, "seconds": 1464, "calls": 0, "dup": "—", "artifacts": "⚠️ 链路故障（0 调用 / 1464s）：TimeoutError: prompt 超时（1440.0s）"}
{"step": 6, "seconds": 1440, "calls": 0, "dup": "—", "artifacts": "⚠️ 链路故障（0 调用 / 1440s）：TimeoutError: prompt 超时（1440.0s）"}
```

但磁盘上 `train.sh`、`train.py`、`outputs/full/`（adapter 40 MB、loss 1.341→0.146）全都在。
**超时只是单次请求的上限，不是任务失败。** 两个维度必须分开记：

| 维度 | 看什么 | 本轮结果 |
|---|---|---|
| 产出物在不在 | 磁盘文件 | ✅ 在 |
| 这一轮问完没有 | calls / 报告落盘 | ❌ 第 7 步报告缺失 |

第 7 步更极端：`progress.jsonl` 最后一行是 `MISSING 05_eval_report.md`，
38 次调用全花在"怎么把 JSON 打印出来"，`step7.md` 里存的是轨迹而不是评测报告。
**评测跑完了，报告没落盘。**

教训：自测脚本判定成功，不能只看"文件存在"，要看是不是该有的那份报告。

### 坑五：评测脚本剥 think 只删了结束标签

第 7 步评测报出 base 0.0%。第一反应是"判分 bug"，但按 2.1 剥离 think 后重算结论不变——
因为剥的方法本身就错了。源码实证（`eval_step7.py`）：

```python
def strip_think(text):
    t = re.sub(r"</think>", "", text)   # 只删闭合标签，思考内容整段留下
    return t.strip()
```

`</think>` 被删了，但 `<think>好的，用户需要我……350 字思考` 还留在字符串里，
意图词判断跑在这段思考上 → base 又是 0。**评测代码在制造假分数。**
正确写法见 `05_verify.md` 2.2：用 `<think>.*?</think>` 连内容一起删，再加单侧标签兜底。

**因果链**：base 0 分 → 是 thinking 配置不同（05_verify 2.1）→ 叠加判分代码剥错（2.2）
→ 两件事都修完，base 分数才有意义。**只修一层，结论还是错的。**

### 坑六：脚本写完了，但没"交付"给用户（用户当场质疑）

用户看了好几轮自测后问："跑了那么多示例，我怎么都没看到具体的训练脚本？"
——这不是没看到，是流程真的没设计"交付脚本"这个动作。

两层都断了：
1. **自测层**：`selftest_e2e.py` 第 5 步只检查 `train.sh` 文件在不在。
   e2e2 那轮文件全在磁盘上、loss 1.341→0.146 跑通了，但 agent 的回复是空的超时错误
   （`step5.md` = `TimeoutError`），用户从头到尾没看到脚本长什么样。
2. **执行层**：报告里只写"已生成训练脚本"也不算——用户拿不到、改不了。

**教训**：**写到磁盘上 ≠ 交付**。交付 = 用户看到全文 + 拿到启动命令 + 知道改哪里。
已修三处：
- `04_train.md` 新增 §7「把脚本交付给用户」：要求贴三件套全文 + smoke/full 命令 + 必改字段
- `00_workflow.md` 第 5 步产出物补 `train.py`（原来漏了训练主体），并写明交付要求
- `selftest_e2e.py` 加 `check_delivery()`：第 5 步回复没贴代码/命令就在 progress 标 ⚠️

同时新增 `templates/`（train.py / train.sh / config.example.yaml）——
实测跑通的版本泛化来的起点，复制后按场景改。**给脚本前先讲清精华和坑**（教学红线），
照抄能跑不等于理解。

### 这一步真正测出了什么

**换任务重跑的价值在于：它暴露的全是"只对某个场景成立"的东西。**

| 只跑案例 16 会以为 | 换任务后才知道 |
|---|---|
| 图脚本能用 | 分钟级任务下时间轴全是 0.0、结论框是方块 |
| `check_dataset` 能识别格式 | 只认 3 种，第 4 种就退化成假数字 |
| base 0% 是判分 bug | 是 thinking 配置不同导致的假对比 |
| 超时 = 失败 | 超时但产出物在，是两件事 |

---

## 用这些案例的方式

- 第 2 步探环境：对着案例 1、2 检查自己的环境与路径选择。
- 第 3 步定方案：对着案例 3 决定"信估算还是信实测"；对着案例 14 检查自己是不是把两个维度折成了一个。
- 第 5 步写脚本：对着案例 4 检查 `target_modules` 是否真的覆盖到了你想训的层。
- 第 5 步测速：对着案例 12 检查你用了多少步，对着案例 13 检查口径是否统一。
- 写任何文档/报告：对着案例 11 检查引用是否被拼接，对着案例 14 检查维度有没有搞混。
- **写技能包/流程文档**：对着案例 15 检查入口三问（我在哪 / 读哪篇 / 读不到怎么办）。
