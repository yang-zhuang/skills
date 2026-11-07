# DOM 与弹窗：Element UI 的选择器、两类弹窗与踩过的坑

## 目的

AutoDL 控制台是 **Element UI（Vue 2）**。这套机器人踩过的坑，八成不是业务逻辑错，
而是「选择器命中的不是你以为的那个元素」。本文把每个关键选择器、**为什么这么写**、
以及弹窗点击的安全边界钉死，改版后照着这张表重新核对即可。

> 给代码片段前先讲清概念（什么时候调用、参数是什么、返回什么、和上一步怎么叠加），
> 不做无评估的搬运。文末有「全盘吸收 / 看业务调整 / 该抛弃 / 埋的雷」。

---

## 一、关键选择器速查表

| 目标 | 选择器 / 方法 | 说明 |
|---|---|---|
| 地区 / 计费 / 数量 | `.el-radio-button__inner` | 地区项文本以「区」结尾，可与其他单选组区分 |
| GPU 型号复选框 | `label.el-checkbox:has(input[value='型号'])` | 型号名整体精确匹配；`vGPU-48GB-350W` 与 `vGPU-48GB` 是两个独立型号，**绝不做前缀/包含匹配** |
| 型号余量预检 | 复选框旁 note 文本如 `(0/152)` | **只读**，为 0 或无此型号的地区直接跳过、不产生点击 |
| 机器卡片 | `.list-item` | 每台一张卡 |
| 空闲数量 | `.gpu-num .num-bold` | |
| 可租按钮 | `button.el-button--primary:has-text('可租')` | 点击后进入创建页 `/create/<区>/<机器id>/1/payg` |
| 镜像选择 | `el-cascader` **四级** | 框架名称 → 框架版本 → Python → CUDA；点 PyTorch 后子面板才渲染，每级点「最后一个可见菜单的第一个节点」 |
| 实例行 | `tr.el-table__row` | 实例列表页 `/console/instance/list` |
| 实例 ID | 正则 `\b([a-z0-9]{8,10}-[a-f0-9]{8})\b` | 从行文本里抽 |
| 状态 | `.status span` | 「运行中 / 已关机 / 开机中 / 排队中」 |
| 地区 | `.region` | |
| **资源空闲标记** | `.gpuTips` | **只读信号**：主机有空闲卡时才有文本；为空时不开机 |
| 距释放时间 | 行文本 `N天N小时N分后释放` | 解析成 `remaining_min`，按最久优先复用 |
| 更多菜单 | `.el-dropdown-menu__item` | 「扩容数据盘」「设置定时关机」等 |
| 弹窗 | `.el-message-box:visible` / `.el-dialog:visible` | **必须带 `:visible`** |
| 行内按钮 | 遍历 `row.locator("button").all()` + `inner_text().strip()` | 见坑 2 |

---

## 二、两类弹窗：这是费用安全的分水岭

点「开机」后可能弹**两种完全不同**的框，混淆它们会直接造成误扣费或新建实例。

### 类型 A：「该主机GPU不足」提示框（无费用、无副作用）

- 文案：`该主机空闲GPU不足，实例GPU需求数量：1 卡，主机GPU空闲数量：0 卡。
  您可以：1. (推荐)克隆实例 2. 无卡模式开机 3. 等待GPU释放`
- 底部**只有一个按钮：「我知道了」**（低分辨率截图容易误读成「我已知晓了」）。
  点它**仅关闭弹窗**，不执行任何操作。
- 正确判定：`if dlg_txt and "GPU不足" in dlg_txt: return "no_slot"`，本轮跳过、等下一轮。
- ⚠️ 文案里提到的「**克隆实例**」会新建一台计费实例（ID 不同、额外扣费）；
  「**无卡模式开机**」没有 GPU，跑不了模型。**这两个入口绝对不能点**。

### 类型 B：「确认开机吗？」计费确认框（会开始计费）

- 文案：`确认开机吗？实例将按 ¥1.78/时整点扣费` → 按钮「**确定**」。
- 点了就真的开机并开始计费。实战 02:13:31 弹的就是这个，02:14:01 已「运行中」。
- ⚠️ **`power_on_poll.py` 的 `SAFE_BUTTONS` 白名单包含「确定 / 确认 / 立即开机」，
  即会自动点这个计费框。** 这只在用户明确授权「把这台机器开机」时成立。
  把脚本复用到别的实例前，务必先确认授权；要改成无人值守通用版，先补人工闸门。

### 白名单为什么是那个顺序

```python
SAFE_BUTTONS = ("我已知晓了", "已知晓", "我知道", "等待GPU释放", "确定", "确认", "立即开机")
```

`button:has-text('我知道')` 是**子串匹配**，能命中「我知道了」。把「我知道」类放在
「确定 / 确认」**前面**，是为了保证类型 A 弹窗先命中关闭按钮，不会误落到别的按钮上。
遍历白名单、命中第一个可见按钮就点并返回；全不命中时兜底点右上角
`.el-message-box__headerbtn, .el-dialog__headerbtn`；还不行就打日志要求人工处理。

**概念说明**：`dismiss_dialog(page, tag)` 在「点击开机之后、轮询状态之前」调用，
参数是 Playwright 的 `page` 和一个日志标签；返回弹窗文本（无弹窗返回 `None`）。
上层用返回值里有没有 `"GPU不足"` 来决定本轮是 `no_slot` 还是继续等状态变化——
这就是它和主循环的叠加方式。

---

## 三、逐个坑与根因

### 坑 1：15 个隐藏的 `.el-dialog`（最坑的一个）

```python
# 错：会命中隐藏元素，真弹窗永远点不到
box = page.locator(".el-dialog:has-text('开机')").first
# 对：只认可见弹窗
box = page.locator(".el-message-box:visible, .el-dialog:visible").first
```

实例页 DOM 里同时存在 **15 个隐藏的 `.el-dialog`**（各种功能面板预渲染）。
`.first` 会命中隐藏的那个 → 按钮找不到 → 弹窗留在屏幕上 →
**`el-overlay is-message-box` 遮罩层拦截后续一切点击**，日志里表现为
`intercepts pointer events`，之后每一轮点「开机」都被挡住，死循环卡死。
修复后还加了**每轮开始先 `dismiss_dialog` 自愈**，清掉上一轮残留弹窗。

### 坑 2：`button:text-is('关机')` 匹配 0 个

按钮文本在**子级 `span`** 里，`text-is` 精确匹配整个元素的文本会失败。
正确写法是遍历行内按钮、逐个取 `inner_text().strip()` 做精确比较：

```python
def find_row_button(row, exact_text):
    for b in row.locator("button").all():
        try:
            if b.inner_text(timeout=500).strip() == exact_text and b.is_visible():
                return b
        except Exception:
            continue
    return None
```

这个坑在 `shutdown_instance.py` 和 `expand_data_disk.py` 各踩过一次。
「关机」和「设置定时关机」是不同按钮，精确比较也是防误点的必要手段。

### 坑 3：表格重渲染后旧的 `row` 引用失效

扩容确认、状态变化都会让 Element UI 表格重新渲染，之前拿到的 `row` Locator 就废了。
`wait_shutdown_then_power_on` 每一步都按实例 ID **重新 `find_row()` 定位**，不复用旧引用。
详见 `disk-expand-and-power-cycle.md`。

### 坑 4：`parse_instance_rows` 必须按 ID 去重

Element UI 的**固定列会克隆行**，同一实例在 DOM 里出现两次。
不按 ID 去重会把「运行中数量」算多、误判触达上限。实现里用 `seen` 集合，
保留 DOM 序靠前的主表格行。

### 坑 5：镜像是四级级联，子面板延迟渲染

`configure_image()` 里点 `PyTorch` 之前，后三级面板根本不在 DOM 中。
必须**点完一级再查「最后一个可见菜单的第一个节点」**，不能一次性抓四个面板。
若 AutoDL 调整了默认排序，这组逻辑要重查（属已知限制）。

### 坑 6：找到机器必须立刻点，别先扫完 10 个地区

早期 `iterate_regions` 会**先把所有地区扫完**再全局择优返回（约 45 秒），
结果机器在这 45 秒里被别人抢走。改成**地区内按「每GPU内存↓ → 可扩容↓」排序，
找到就立刻返回并点「可租」**。另外点击本身要包 `try/except` + `timeout=5000`：
实战 20:26 那次抓到 350W 但点击超时 10 秒直接把进程崩了，现在超时就继续下一轮。

### 坑 7：`create_machine` 的假成功

曾经即使没点到「创建并开机」也返回 `created`。现在返回值严格区分
`created` / `insufficient`（主机卡不足，回市场重找）/ 其它，
上层据此决定是否重新盘点实例。`DRY_RUN=true` 时根本不走到点击。

### 坑 8：扩容输入框长时间 `disabled`

`.el-dialog:has-text('扩容')` + `box.fill()` 会等 30 秒超时。
根因是 Element UI 初始化时序，必须 JS 强制启用 + 按 Vue 方式派发事件。
见 `disk-expand-and-power-cycle.md`。

---

## 四、页面改版后的标准动作（零费用）

1. `python inspect_instances.py` / `inspect_market.py` / `inspect_create_page.py`
   —— dump 真实 DOM，**只读**，绝不点「创建并开机」。
2. `python test_selectors.py` / `test_find_machine.py` / `test_instance_check.py`
   —— 只读回归：验证选择器、型号优先级、实例解析与决策预演，不点任何计费按钮。
3. `python test_model_priority.py` —— **不开浏览器**的本地单元测试（型号归一化 / 排序规则）。
4. `python diagnose_filter.py` —— 诊断「勾选型号后切换地区，筛选是否仍生效」。
5. 核对完再改 `rent_bot.py` 里的选择器常量，**不要拿真实下单流程当调试手段**。

---

## 五、复用前必读：怎么吸收这些代码

| 维度 | 内容 |
|---|---|
| **全盘吸收** | `:visible` 过滤、按钮文本精确比较、两类弹窗白名单与顺序、按 ID 重新定位行、找到即点。这五件是「不误扣费 + 不卡死」的地基。 |
| **看业务调整** | 选择器字符串（页面改版必然变）、`SAFE_BUTTONS` 白名单（新文案要补）、型号名与排序权重、弹窗判定关键词 `"GPU不足"`。 |
| **该抛弃** | 硬编码的实例 ID（`TARGET_ID`）、`inspect_*` 导出的 `*_full.html` 快照；诊断脚本的本机绝对路径在收录版里已改为按当前用户 / 脚本目录推导，不必再照抄旧写法。 |
| **埋的雷** | 白名单含「确定」会**自动确认计费**；`has-text` 是子串匹配，新增按钮文案要先验证不会误命中；遮罩层不清理会让后续所有点击静默失败（日志看起来像「主机资源不足」，其实是本地被拦截）。 |

---

## 附录：诊断证据怎么留

- `dismiss_dialog` 会打印弹窗全文前 80 字并截图到 `screenshots/power_on_dialog.png`；
- `power_on_poll.log` 每行带时间戳、每行 flush，被强杀也能看到最后一轮发生了什么；
- 异常轮会截 `screenshots/poll_error.png`（`full_page=True`），成功截 `poll_success.png`；
- 所有实例操作（开机 / 租到 / 扩容 / 关机 / 失败）写入 `instances_log.json`，
  字段含 `time` / `event` / `id` / `region` / `status` / `by`，**不含任何账号凭证**。
