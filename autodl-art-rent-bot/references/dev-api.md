# 开发者 API：鉴权、端点与安全边界

> 2026-11-24 实测。来源：官方文档 <https://autodl.art/docs/app_api/>，并用真实 token 验证过
> `list` 端点返回 `Success`。这是**官方文档写明的接口**，不是从 JS 里逆出来的内部接口。

---

## 一、鉴权

| 项 | 值 |
|---|---|
| HOST | `https://www.autodl.art` |
| Token 获取 | 登录 `www.autodl.com` → 控制台 → 账号 → 设置 → 开发者 Token |
| 请求头 | `Authorization: <token>` |
| 有效期 | 未在文档说明；失效表现为 `code=AuthorizeFailed`，去控制台重新生成 |

**实测的坑**：`Authorization: token <token>`（带 `token ` 前缀）会返回
`AuthorizeFailed / 登录超时`；只放裸 token 才是对的。`autodl-rent-bot` 里网页 JWT 的用法
和这里不同，别混。

Token 存包根 `.env` 的 `AUTODL_DEV_TOKEN`，`.env` 已在 `.gitignore` 里。代码只从环境变量
读，不写死、不回显明文（`dev_check.py` 只打印长度）。

---

## 二、端点清单（全部实测或来自官方文档）

| 用途 | 方法 | 路径 | 关键参数 | 是否花钱/改状态 |
|---|---|---|---|---|
| 创建实例 | POST | `/api/v1/adl_dev/dev/instance/pro/create` | `gpu_spec_uuid`、`application_uuid`、`application_version`、`req_gpu_amount`、`expand_system_disk_by_gb` | **花钱** |
| 实例列表 | POST | `/api/v1/adl_dev/dev/instance/pro/list` | `page_index`、`page_size` | 只读（已实测 Success） |
| 实例详情 | GET | `/api/v1/adl_dev/dev/instance/pro/snapshot` | `instance_uuid`（**query**） | 只读 |
| 实例状态 | GET | `/api/v1/adl_dev/dev/instance/pro/status` | `instance_uuid`（**query**） | 只读 |
| 开机 | POST | `/api/v1/adl_dev/dev/instance/pro/power_on` | `instance_uuid`、`payload="gpu"` | 改状态，产生计费 |
| 关机 | POST | `/api/v1/adl_dev/dev/instance/pro/power_off` | `instance_uuid` | 改状态 |
| 释放 | POST | `/api/v1/adl_dev/dev/instance/pro/release` | `instance_uuid` | **不可逆销毁** |

> ⚠️ **文档与实现不一致的坑（2026-11-24 实测）**：官方文档把 `status`/`snapshot` 写成
> 「GET + JSON body」，但服务端实际**只认 query 参数**。发 JSON body 返回
> `RequestParameterIsWrong / 请求参数错误`；改成 `GET ?instance_uuid=...` 才返回正常结果
> （不存在时是 `RecordNotFoundError / 未查询到相关实例`）。`dev_api.py` 已按实测用 query。

`snapshot` 返回 `ssh_command`、`root_password`、`ssh_port`（端口）、`jupyter_domain` 等，
机器到手后的连接信息从这里拿。

---

## 二点五、能选什么、不能选什么（重要）

两个站的官方 create 接口都**只能选到「卡型 + 地区」的粒度，不能指定具体主机**。

| 维度 | 官方 API 能否指定 | 字段 |
|---|---|---|
| GPU 卡型 | ✅ 能 | `gpu_spec_uuid` |
| 地区 | ✅ 能（选填） | `data_center_list` |
| GPU 数量 | ✅ 能 | `req_gpu_amount` |
| 具体某一台主机 | ❌ 不能 | 无此参数 |

`list` 返回里的 `machine_id` / `machine_alias` 是**创建后告知落在哪台**，不是可选入参。

### 所以 art 官方 API 不存在「抢机器」

- **抢** = 盯某台主机、等 GPU 释放、一空闲立刻开机；依赖「主机级空闲信号」。
- **下单** = 按规格+地区提交创建，有资源就分配，没资源就失败。

art **dev API** 只有「下单」，没有库存端点。**但库存信号在网页接口里是存在的**（2026-11-24 从
`assets/private-image.b603628c.js` 的 GPU 选择器组件逆出）：

```
POST /api/v1/adl_server/machine_pro/gpu_type
body: {"data_center_list": [选中的地区, ...]}
返回: [{ "gpu_type": "3090-48G", "gpu_idle_num": 0, "gpu_total_number": 128, ... }]
                                     ↑ 空闲数    ↑ 总数  ← 部署页按钮上的 "(0/128)" 就是这两个字段
```

**关键限制：这个接口不吃开发者 token。** 实测三种情况：
| 凭据 | 结果 |
|---|---|
| 匿名（各种参数组合） | `RequestParameterIsWrong`（中间件先拦，不是参数真错） |
| 开发者 token | `AuthorizeFailed`（它认的是网页登录态，不是 dev 凭据体系） |
| 网页登录 JWT | 未实测，但源码证实可行：`index.cf7abf41.js` 的请求拦截器给所有请求加 `Authorization: getToken()`，getToken() 读浏览器登录 JWT |

推论：**「有库存信号才点」的精准抢法（对齐 autodl-rent-bot）需要网页登录 JWT**（浏览器
F12 → Application → Local Storage 的 `token` 键）。已实现**全自动提取**：
`scripts/extract_jwt.py` 用 Playwright 专用 profile（`.browser_profile/`，已 gitignore），
无头先试读 localStorage，读不到才弹窗口让你登录一次；之后每次运行自动刷新，无需人工。
配合 `grab.py create --signal` 即可轮询 `gpu_type`、`gpu_idle_num > 0` 时才调 create——
无效请求趋近于零。只有 dev token 时只能盲试（`grab.py` 现状，退避机制兜底）。

### 为什么 `autodl-rent-bot` 用 DOM

autodl.com 算力市场是**主机粒度**、稀缺卡要等其他用户关机释放，那个「这台主机有没有空卡」
的信号只渲染在网页 `.gpuTips` 里，官方 API 没暴露。所以那边只能开浏览器读 DOM。
art 的应用部署是按规格调度，库存信号有独立 JSON 接口（见上），**拿到网页 JWT 后连 DOM 都不用读**。

### 认证限制

`autodl.com` 的 Pro API 文档明写：**使用前需个人实名认证或企业认证**。art 文档未写此条，
但两者共用同一个开发者 token 和账号资质。

---

## 三、GPU 规格 ID 对照（官方文档附录，硬编码）

| 规格 ID（填 API） | 网页显示名 | 类型 |
|---|---|---|
| `h800` | H800-80G | 通用型 |
| `v-48g` | 4090-48G | 通用型 |
| `pro6000-p` | PRO6000-96G | 性能型 |
| `v-32g-p` | 4080(S)-32G | 性能型 |
| `v-48g-350w` | 3090-48G | 通用型 |
| `5090-p` | 5090-32G | 性能型 |

**没有库存/价格查询端点。** 实时库存和按量价格只能去网页部署页看。所以
`select_gpu.py` 只做名称对齐，不假装能查库存。

---

## 四、`application_uuid` 从哪来

抓详情接口 `POST /api/v1/application/square/detail` 返回的 `uuid` 就是
`application_uuid`。例：id=273「ComfyUI中文整合版」→ `ioBLIFWG1b`。

`deploy.py --app-id 273` 会自动从 `outputs/app_details.json` 取这个 uuid，不用手抄。
所以**部署前必须先跑 `fetch_details.py` 把目标应用的详情抓下来**。

---

## 五、安全边界（关键）

1. **脚本永远不碰登录表单。** 不输账号密码、不过滑块验证码。token 是人去控制台生成的
   一个字符串，脚本只带它，不参与认证过程。这是整条链路最安全的地方。
2. **花钱动作必须显式 `--yes`。** `deploy.py` 默认 dry-run 只打印 payload；
   `power_ctl.py release` 不加 `--yes` 直接拒绝。
3. **不做自动重试。** 创建实例是幂等性不明的动作，网络抖动时宁可人工重跑，也不自动补发，
   避免一次操作建出两三个计费实例。
4. **不并发、不批量循环创建。** 一次一个，人工确认。
5. **release 前先 power_off**（文档明确要求），否则可能释放失败。

---

## 六、排错

| 现象 | 原因 / 处理 |
|---|---|
| `AuthorizeFailed / 登录超时` | token 错、漏字符、或加了 `token ` 前缀；重新生成，只放裸 token |
| `404 page not found` | 路径拼错；照上表逐字符核对 |
| 创建返回 `Success` 但没实例 | 用 `power_ctl.py list` 核实；可能是地区无库存，换 `--region` |
| 想查实时库存 | 本 API 没有该端点，去网页部署页看 |
