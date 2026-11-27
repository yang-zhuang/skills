# 边界与演进：从「只选应用」到「用官方 API 部署」

> 本文记录这条链路的两个阶段，以及每个阶段的安全边界。2026-11-24 更新：阶段二已实现。

---

## 阶段一（已实现）：零登录抓取 + 大模型择优 + 人工终选

用户截图对应的动作链：

1. 打开 `https://www.autodl.art/app/market`，顶部选中「应用」；
2. 默认排序「全部(243)」，下面一排标签（漫剧、二次元、LTX-2、语音……）；
3. 点应用卡片弹详情（简介、收藏、更新时间、应用版本、应用 ID、开机命令、部署应用按钮）；
4. 在右侧「创建实例」选地区、GPU 型号和数量；
5. 点「部署应用」；未登录会弹登录框。

第 1~3 步的资料，本 skill 已用**匿名接口**全量拿到，不需要浏览器、不需要登录：
`fetch_apps.py` → `fetch_details.py` → `select_apps.py` → `choose_final.py`。

---

## 阶段二（已实现）：开发者 API 选卡 + 部署

第 4~5 步需要账号和费用决策。现在用**官方开发者 API** 实现，比浏览器点击干净：
脚本只带一个 token，从不碰登录表单、不模拟浏览器、不处理验证码。

### 端到端证据（2026-11-24 实测）

| 项 | 结果 |
|---|---|
| 鉴权格式 | `Authorization: <裸token>` → `Success`；加 `token ` 前缀 → `AuthorizeFailed` |
| `list` 端点 | 真实 token 调用成功，账号下 0 个实例 |
| 官方文档 | <https://autodl.art/docs/app_api/>，端点与参数齐全 |
| 规格 ID | 文档附录硬编码 6 个（见 `dev-api.md`） |
| 库存/价格查询 | **官方 API 没有此端点**，只能去网页部署页看 |

### 脚本

- `dev_check.py` — 验证 token、列实例（只读）
- `select_gpu.py` — 规格 ID ↔ 网页显示名对照
- `deploy.py` — 创建实例，**默认 dry-run，`--yes` 才花钱**
- `power_ctl.py` — list / status / snapshot / on / off / release（release 需 `--yes`）

---

## 前端内部接口（仅记录，不用于部署）

`assets/deploy.3afb0f48.js` 里还有一整套网页自用的下单接口：

| 用途 | 方法 / 路径 |
|---|---|
| 机器型号 | `POST /api/v1/adl_server/machine_pro/gpu_type` |
| 规格配置 | `POST /api/v1/adl_server/machine_pro/gpu_spec_config` |
| 价格预览 | `POST /api/v1/adl_server/order/price/preview/pro` |
| 按量创建 | `POST /api/v1/adl_server/order/instance/pro/create/payg` |
| 订单支付 | `PUT /api/v1/adl_server/order/pay` |

这些是网页内部接口，**本 skill 不用**——有官方 dev API 就不去逆内部下单链路。
实测空请求体 `{}` 返回 `RequestParameterIsWrong`，说明参数复杂；不再试探。

---

## 安全边界（两个阶段共同遵守）

1. **抓取不登录**：阶段一不碰 Cookie/JWT、不点登录、不过验证码。
2. **部署只带 token**：token 是人去控制台生成的一串字符，脚本不参与认证过程。
   账号密码/验证码永远不进脚本。
3. **花钱动作默认关闭**：`deploy.py` 不加 `--yes` 只 dry-run；`release` 不加 `--yes` 直接拒绝。
4. **不自动重试创建**：创建实例幂等性不明，网络抖动时人工重跑，避免建出重复计费实例。
5. **人工终选 + 人工确认参数**：`final_choice.json` 由人确认；`deploy.py` 执行前回显完整 payload。
6. **release 前先 power_off**（文档要求）。
7. **产物与凭据不入库**：`outputs/`、`.env`（含 token）、日志一律 gitignore。

---

## 已知限制

- 官方 API 无实时库存/价格端点，选卡时价格与库存以网页部署页为准。
- `snapshot` 返回的 SSH 密码等敏感信息只打印，不落盘。
- art 站没有长时间登录态自动化运行的实测数据；autodl.com 的 9.75 小时实测
  （见 `autodl-rent-bot/references/zero-login-and-anti-ban.md`）不能直接当 art 的保证。

---

## 产物终点

```
outputs/apps.json          -> 全部应用（默认排序）
outputs/app_details.json   -> 已抓详情的应用（含 application_uuid，部署要用）
outputs/selection_*.json   -> 大模型 TopN 排序与理由
outputs/final_choice.json  -> 人工终选
deploy.py --yes            -> 创建实例（花钱，需显式确认）
```
