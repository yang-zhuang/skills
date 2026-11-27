# 匿名接口：发现过程、字段与改版排查

## 一、怎么找到这些接口的（可复现）

1. `curl https://www.autodl.art/app/market` 只拿到 1835 字节的 SPA 外壳，正文由 JS 渲染；
2. 外壳里 `<script type="module" src="/assets/index.cf7abf41.js">`，下载后搜 `/api/v1`：
   找到登录相关接口和 `url$1` / `url` 两张接口表；
3. 路由表里 `name:"app-market"` 对应懒加载 chunk `assets/index.a5d27239.js`，
   `name:"app-details"` 对应 `assets/app-details.e662a896.js`；
4. 这两个 chunk 的接口函数是从 `assets/deploy.3afb0f48.js` import 的，那里集中定义了
   `POST /api/v1/application`、`POST /api/v1/application/square/detail` 等；
5. 标签接口来自 `assets/tag.b8774455.js`：`GET /api/v1/unified_tag/tree`；
6. 用 curl 直接匿名请求验证，全部返回 `{"code":"Success", ...}`，不需要 cookie、不需要登录态。

结论：**抓应用资料这件事不用开浏览器**。Playwright 只适用于下一阶段「点部署」。

## 二、四个已验证接口

| 接口 | 方法与请求体 | 2026-11-24 实测 | 关键返回字段 |
|---|---|---|---|
| 应用列表 | `POST /api/v1/application`<br>`{"page_index":1,"page_size":100,"name":"","unified_tag_ids":[]}` | 200 Success；result_total=243，max_page=3，单页 100 条正常 | `list[]`: id, name, username, nickname, star_num, describe, version_list, logo, avatar, runtime, download_num, use_num, popular_type |
| 应用详情 | `POST /api/v1/application/square/detail`<br>`{"application_id":273}` | 200 Success | 列表全部字段 + `readme`（全文 markdown）、`author_note`、`tag_list`、`latest_version`、`latest_version_updated_at`、`uuid`、完整 `version_list` |
| 标签树 | `GET /api/v1/unified_tag/tree` | 200 Success；3 个一级分类、66 个末级标签 | 三级结构：`name / name_en / id / children[]`；末级 id 用于 `unified_tag_ids` 过滤 |
| 周榜 | `POST /api/v1/application/runtime/week`<br>`{}` | 200 Success；10 条 | 运行时长榜，脚本只做探查，不参与默认排序 |

最小 curl 例子（可直接复制验证）：

```bash
curl -sS -X POST "https://www.autodl.art/api/v1/application" \
  -H "Content-Type: application/json" -H "Origin: https://www.autodl.art" \
  -d '{"page_index":1,"page_size":3,"name":"","unified_tag_ids":[]}'

curl -sS -X POST "https://www.autodl.art/api/v1/application/square/detail" \
  -H "Content-Type: application/json" -H "Origin: https://www.autodl.art" \
  -d '{"application_id":273}'
```

## 三、版本列表字段（`version_list[]`）

一个应用常有多个历史版本，详情页给全，列表页也给全：

`id`、`application_id`、`image_version`、`framework`、`framework_v`、`cuda_v`、`start_command`、
`chip_corp`、`cpu_arch`、`image_size`（字节）、`image_status`、`review_status`、`describe`、
`download_num`、`runtime`、`private_image_uuid`、`private_image_name`、`created_at`、`updated_at`。

`prompt.py` 只取前 3 个版本做摘要，避免把提示词撑爆；`app_details.json` 保留完整版本列表。

### 应用级字段的两个坑（2026-11-24 补）

- `popular_type` 不是普通标签：前端 `assets/app-item.js` 用
  `popular_type === "excellent" ? "精" : "热"` 渲染卡片角标。实测 243 个应用里 excellent=9、hot=4、空=230。
- **详情页 UI 显示的「更新时间」取应用级 `updated_at`**（`app-details.js` 用 `a.value.updated_at`），
  不是 `latest_version_updated_at`。截图里 id=273 显示 2026-11-23 11:57:01，正是应用级 `updated_at`；
  id=112 的应用级 `updated_at` 是 2026-01-28，`latest_version_updated_at` 却是 2026-01-24。
  本 skill 统一用 `updated_at` 做陈旧判断；列表接口没有应用级 `updated_at`，才从 `version_list` 的最大
  `updated_at` 推算并标注来源。

## 四、限速与礼貌

- 虽然匿名，仍按 1.0~2.5 秒随机间隔请求（`config.py` 的 `MIN_DELAY` / `MAX_DELAY`），失败最多重试 2 次；
- 243 条详情全抓约 4~10 分钟，**不要**并发打接口，也不要把间隔调到 0；
- 抓详情支持断点续抓：已有 id 直接跳过，重跑不会重复请求；
- 每次真实抓取都落盘到 `outputs/`，爬一半中断也不会丢已抓结果。

## 五、页面/接口改版后怎么排查

1. `python inspect_api.py` → 看四个接口是否仍 `Success`、字段是否还在；产物在 `outputs/probe/`；
2. 如果接口路径变了：重新下载 SPA 外壳里的 `index.*.js`，按第一节的方法找新 chunk 和新路径，
   改 `scripts/config.py`，不要直接改各个 fetch 脚本；
3. 如果字段改名：改 `scripts/prompt.py` 的 `build_app_summary()`，其他脚本不需要动；
4. 如果接口开始要求登录：**停在这里**，不要试图补 Cookie/JWT。本 skill 的设计前提就是零登录；
   需要登录说明目标已经变了，应转人工。
