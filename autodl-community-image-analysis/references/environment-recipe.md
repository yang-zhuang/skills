# 环境配方：从最基础的 AutoDL 镜像还原

## 目的

回答「给定一台最基础的 AutoDL 机器，要装什么、装哪个版本、怎么启动，才能还原这个镜像」。
这一节是**探测动作模板**，按当前问题取用，不是「每个镜像必查这九项」的清单；换镜像只重跑受实例影响的探测。

**硬规则**：先实测再写还原步骤。写不出来的项标「未核实」，不要凭镜像名编 GitHub 地址或版本号。
「实测结果」列里凡带机器号、路径、单次数值的，都是**该实例该时刻的一次性值**，不可进 skill 当通用规格。

## 探测 → 实测 → 还原动作

| 维度 | 探测命令（原样可复制） | 本次实测（仅该实例） | 还原动作 |
|---|---|---|---|
| 系统与驱动 | `cat /etc/os-release \| head -n 4`；`nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader` | Ubuntu 22.04.5；RTX 3090 49152 MiB；驱动 595.91.07 | 选基础镜像时对齐系统版本与驱动大版本；驱动不可在容器内随意升级 |
| Python 与包管理器 | `/root/miniconda3/bin/python -V`；`/root/miniconda3/bin/conda env list` | Python 3.12.3；只有 `base` 一个环境 | 用基础镜像自带 miniconda3 的 base，别再建 venv/conda env 叠加 |
| Torch / CUDA | `/root/miniconda3/bin/python -c "import torch; print(torch.__version__, torch.version.cuda)"` | 2.12.1+cu130 / CUDA 13.0 | 安装时锁死 `torch==2.12.1+cu130` 与 `torchvision==0.27.1+cu130`，CUDA 小版本不对会导致加速补丁失效 |
| ComfyUI 本体（github 地址来源） | `git -C /root/ComfyUI remote -v`；`git -C /root/ComfyUI rev-parse HEAD`；`git -C /root/ComfyUI describe --tags --exact-match HEAD` | remote `https://github.com/Comfy-Org/ComfyUI.git`；commit `6e8488ef986067631e9c86e2a14360644b105663`；tag `v0.45.2`（HEAD detached at v0.45.2） | `git clone` 后 `git checkout v0.45.2`（等价 commit）。**GitHub 地址确实查得到，来源是本体仓库；只写实测到的 remote + tag/commit，不写镜像展示名猜的地址** |
| ComfyUI 工作区改动 | `git -C /root/ComfyUI status --short`；`git -C /root/ComfyUI diff --stat`；`git -C /root/ComfyUI diff -- server.py` | 3 处改动：`server.py` +74、`middleware/cache_middleware.py` +34、删除 `output/_output_images_will_be_put_here`；补丁自带标注 `zealman-web-speed`：`/object_info` 缓存、gzip 去重、`/assets/` 长期缓存 | **关键坑：只 checkout tag 还原不了镜像**。还原要先把 `git diff` 存成补丁（如 `git diff > zealman-web-speed.patch`）再应用到同版本仓库；否则得到的只是上游原版。这些改动影响界面/API 响应，也是「提速」的一部分 |
| 自定义节点 | `cat /root/ComfyUI/custom_nodes/.custom_nodes_bundle_version`；`head -c 1500 /root/ComfyUI/custom_nodes/.zealman-plugin-profile.json`；`find /root -maxdepth 4 -type d -name .git` | bundle 版本 `202608081800`；节点清单在 profile json 的 `seenPlugins`；**只有 ComfyUI 本体有 `.git`**，节点目录没有 | 关键坑：节点是整包分发、不是逐个 `git clone`，**没有 remote 可取**。还原只有两条路：从同版本镜像整包拷贝节点目录，或按节点目录里的 `README` / `pyproject.toml` 逐个找上游。查不到就写「上游未核实」，不要编造 |
| Python 依赖 | `/root/miniconda3/bin/pip freeze \| wc -l`；`/root/miniconda3/bin/pip freeze \| grep -iE 'torch\|transformers\|triton'` | 536 个包；`transformers==4.57.1`、`triton==3.7.1`、`comfyui-manager==4.2.2`；大量行是 `file:///root/autodl-fs/依赖/comfyui-plusing/*.whl` | 关键坑：`pip freeze` 里的 `file://` 本地 wheel 路径在别的机器上不存在，**不能直接 `pip install -r`**。先把这些行换成 PyPI 包名+版本，或连 wheel 目录一起拷走；用 pip，不要用 uv |
| 编译型加速包 | `/root/miniconda3/bin/pip freeze \| grep -iE 'sage\|flash\|nunchaku\|cupy'` | `sageattention @ file:///root/build_sageattn/SageAttention`（本地源码构建）；`flash_attn` 2.8.3+cu13 本地 wheel；`nunchaku` 本地 wheel；`cupy-cuda12x` 14.1.1 | 这类包绑定 GPU/CUDA/Torch，换卡要重新找对应 wheel 或重编，不能照抄版本号；Record 为「需重建」而非「照装」 |
| 启动方式 | `ps -ef \| grep -E 'main.py\|server.js\|supervisord' \| grep -v grep` | ComfyUI：`main.py --port 6006 --listen 127.0.0.1 --enable-cors-header *`；另有 node 面板、jupyter-lab、tensorboard 6007、supervisord | 还原启动命令并逐项对齐参数；端口与监听地址必须实测，不要假定默认 |
| 模型挂载 | `mount \| grep -i autodl` | `/.autodl-model/data` 是 AutoFS **只读**挂载 | 公共模型库只能读、不能写；还原时建软链即可，往里写会失败 |

## 同一问题的多条探测路（主路径优先，备选看条件）

上表给的是本次跑通的主路径。同一个问题常有多种探测法：**主路径没结果不等于「未核实」，先试备选再下结论**。按手头有什么取用，不要堆成一个必查清单。

| 想知道 | 主路径（本次用的） | 备选路径 | 什么时候走备选 |
|---|---|---|---|
| 节点上游 GitHub 地址 | 节点目录 `git remote -v` | `grep -iE 'github\|homepage\|repository' <节点>/pyproject.toml`；`head -n 20 <节点>/__init__.py` 看头注释；ComfyUI-Manager 的 `node_db`；镜像说明/README 里的链接 | 节点是整包 bundle、没有 `.git` 时（本次即是） |
| ComfyUI 版本 | `git describe --tags --exact-match HEAD` | `git rev-parse HEAD` + `git log -1`；`/system_stats` 的 `comfyui_version`；镜像说明里的版本号交叉验证 | 仓库无 tag，或运行时版本与源码版本不一致时 |
| 工作区是否被改过 | `git status --short` + `git diff --stat` | 对比 `/system_stats` 与源码 tag；`git stash list`；直接读 `middleware/` 与 `server.py` 关键段落 | 镜像行为与上游版本对不上时（如 `/object_info` 缓存、额外 gzip） |
| 依赖来源与范围 | `pip freeze` | `pip list --format=freeze`；`conda list --explicit`；镜像自带的 `requirements*.txt` / `environment*.yml` | `pip freeze` 含大量 `file://` 行，或要区分直接依赖与传递依赖时 |
| 启动命令 | `ps -ef` 看 cmdline | 读 supervisor 配置；`tr '\0' ' ' < /proc/<PID>/cmdline`；容器/面板启动脚本 | 进程名看不出完整参数，或启动后被父进程改写时 |
| 模型实际搜索路径 | `find /root/ComfyUI/models -name '<文件>'` | `cat /root/ComfyUI/extra_model_paths.yaml`；`grep -n 'models' folder_paths.py`；API `/object_info` 里的模型枚举值 | 模型不在默认目录，或工作流引用了外部路径时 |

## 还原顺序（一条路，不搞参数切换）

1. 选基础镜像 → 对齐系统/驱动/Python/CUDA 四项。
2. `git clone` ComfyUI 并 checkout 到实测 tag/commit，再应用第 4 步导出的工作区补丁（先做这一步：在源机器 `git diff > zealman-web-speed.patch`）。
3. 放入节点 bundle（或逐个补上游，查不到就标未核实）。
4. 装依赖：先处理 `file://` 行，再装 PyPI 包，最后单独处理编译型包。
5. 建模型软链（见 `model-recipe.md`）。
6. 按实测 cmdline 启动，用 `/system_stats` 验收。

**导出本地补丁（还原必做）**：镜像若对工作区源码有改动，只 checkout tag/commit 还原不了。
先 `git -C <repo> status --short` 看改动清单，`git -C <repo> diff > <name>.patch` 导出补丁并记
`git describe --tags --exact-match HEAD`（tag 比 commit 好记）；还原时先 checkout 同版本，再
`git apply <name>.patch`。untracked 文件不在 diff 里，要单独拷贝。改动清单里的 `D` 删除项也要还原。

**不要做的事**：不要凭镜像名编造 GitHub 地址；不要把 `pip freeze` 原文当可移植清单；不要在本机新建 conda env 再装一遍 torch。

## 盘位与扩容：用户最关心、也是最容易凭经验做错的一条

AutoDL 系统盘不可扩、数据盘可扩，但**不能假定 `/root/autodl-tmp` 就是数据盘**——本实例实测它落在 `overlay 30G`，879G 的 `/dev/nvme3n1p2` 却以 `ro` 挂在 nvidia 库路径上。

| 判定问题 | 探测命令（原样可复制） | 判定动作 |
|---|---|---|
| 模型权重实际占哪块盘 | `df -h /root/ComfyUI/models`；`du -sh /root/ComfyUI/models`（软链不跟随）；`find /root/ComfyUI/models -type l \| wc -l` | 软链指向的权重大多在**只读公共库**（`mount \| grep -i autodl` 确认 AutoFS `ro`），不占系统盘；先数清软链再算容量账，别把软链当实体重复计 |
| 目标迁移目录是否可写、在哪块盘 | `df -h <目标目录>`；`mount \| grep <挂载点>`；`test -w <目标目录> && echo WRITABLE` | `ro` 就换目录；确认落在可扩的数据盘再动手；扩容后重跑 `df` 验证 |
| 哪些是实体大文件 | `du -ah /root/ComfyUI/models \| sort -rh \| head -n 20` | 只迁实体；本次实体仅 606M，权重主体全在软链里 |

**迁移三步（只动实体）**：① `df -h` 确认目标盘可写且容量够 → ② `mv <实体文件> <数据盘目录>` → ③ `ln -s <新路径> /root/ComfyUI/models/<原相对路径>`。**软链模型不要改**——本来就在公共库，改了反而找不到。输出目录前缀同理，量产前改到数据盘。

**显存被占 ≠ 模型放错盘**：生成结束后 ComfyUI 常驻进程仍持有模型是正常现象；要释放只能重启（丢队列状态）。判显存看 `/system_stats`，判磁盘看 `df -h`，两者不要混为一谈。
