# GitHub 代码与模型权重的下载方式

## 目的

给出 AutoDL 环境下**可靠的下载路径**：GitHub 代码为什么不能用 `git clone`、该用什么替代，
模型权重该用哪个源，以及同一进程里「部分走代理、部分走直连」该如何分流。

---

## 一、GitHub 代码：不要用 git clone

### 现象

走 `network_turbo` 代理时，`git clone` 会命中以下问题：

- git 的 **pack 协议**经代理被限速到约 **10 KB/s**（28MB 的仓库跑了 5 分钟才 12MB）
- 频繁 `early EOF` / `HTTP 503`，克隆中途失败，git 会自动清理残目录
- 换用 `ghproxy.net` 等镜像同样会 `fetch-pack: unexpected disconnect` 而断流

### 结论：改用 codeload tarball + curl

同样走 turbo，但 **curl 下载整包是持续高速的**（实测 2.5–3.9 MB/s）：

```bash
source /etc/network_turbo
curl -sSL --retry 3 -o /root/autodl-tmp/<repo>.tar.gz \
  -w 'code=%{http_code} speed=%{speed_download} size=%{size_download} t=%{time_total}\n' \
  https://codeload.github.com/<owner>/<repo>/tar.gz/refs/heads/main
```

随后解压（解压产物目录名通常为 `<repo>-main`）：

```bash
cd /root/code
tar xzf /root/autodl-tmp/<repo>.tar.gz
ls -la /root/code
```

> 另一分支把 `refs/heads/main` 换成对应分支名即可。

### 先估体积再下

用 GitHub API 查仓库体积（`size` 字段单位为 KB），便于判断余量与预期耗时：

```bash
curl -sS https://api.github.com/repos/<owner>/<repo> | grep '"size"'
```

---

## 二、模型权重：优先 ModelScope 国内源

很多推理项目默认从 HuggingFace 拉权重，而 `huggingface.co` 在国内实例上常不可达。

### 优先使用项目自带的国内源开关

以 Strata 为例，它支持 `--source modelscope`（或环境变量 `STRATA_SOURCE=modelscope`），
会从 `https://www.modelscope.cn` 拉取同样的文件，并用 ModelScope 公布的 SHA-256 校验。

```bash
# 示例：指定国内源 + 指定落到数据盘
./setup.sh --yes --family qwen --model IQ3_S \
  --source modelscope \
  --data-dir /root/autodl-tmp/strata-data \
  --no-start
```

### 备选：HF 镜像

若工具不支持 ModelScope，可设环境变量使用 HF 镜像：

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

> ModelScope 更优先：它是国内源，直连即可，不需代理。

---

## 三、关键：同一进程内的下载分流（no_proxy）

**场景**：安装脚本通常把「pip 装依赖」「下载引擎二进制（GitHub）」「下载模型（ModelScope）」
串在同一个进程里跑。而 turbo 一旦 source，代理会作用于**全部**流量——
模型这种几十 GB 的大流量若被代理处理，有被限速的风险。

**解法**：source 代理的同时，用 `no_proxy` 把国内源域名排除掉：

```bash
source /etc/network_turbo
export no_proxy="www.modelscope.cn,modelscope.cn,localhost,127.0.0.1"
export NO_PROXY="www.modelscope.cn,modelscope.cn,localhost,127.0.0.1"
```

效果：

- pip → PyPI：走代理 ✅ 快
- 引擎 → GitHub Releases：走代理 ✅ 快
- 模型 → `www.modelscope.cn`：**绕过代理直连** ✅ 符合「国内源不用 turbo」

> `requests` / pip 等都会遵循 `no_proxy`，因此该分流能被 Python 安装脚本正确识别。
> 若下载通过 CDN 跳转到了别的域名，可把该域名补进 `no_proxy`。

验证方式：确认下载过程中没有到 turbo 代理地址的 TCP 连接，且速率符合直连预期。

---

## 四、编译场景：toolchain 版本冲突

容器的**系统工具版本往往偏旧**。典型例子：系统 `cmake` 为 3.22.1，而项目要求 ≥ 3.24。

即便 requirements 里已经往 venv 装了 `cmake==4.4.3`，安装脚本仍可能用到系统旧版——
根因是很多脚本这样定位工具：

```python
p = shutil.which(name)   # 先按 PATH 搜索
```

而 Python venv **并不会修改 PATH**，于是 `shutil.which("cmake")` 先命中 `/usr/bin/cmake`（旧版），
venv 里的新版作为「兜底」反而永远轮不到。

**解法：把 venv 的 bin 放到 PATH 最前面**

```bash
export PATH=/root/code/<项目>/.venv/bin:/root/miniconda3/bin:$PATH
which cmake
cmake --version | head -1
```

确认 `which cmake` 指向 `.venv/bin/cmake`（4.x）后再重跑安装器。
多数安装器支持断点续跑（已完成的步骤会跳过），修好环境重跑即可。

---

## 附录：速率参考表

| 场景 | 实测速率 |
|---|---|
| codeload tarball + curl（走 turbo） | 2.5 – 3.9 MB/s |
| ModelScope 国内源直连 | ~5 MB/s（基本为该实例外网上限） |
| git clone（走 turbo） | ~10 KB/s，且频繁断连 ❌ |
| GitHub Releases 404 | 多为代理未正确处理跳转造成的**假 404**，不代表资源不存在 |
