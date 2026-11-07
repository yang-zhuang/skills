# 数据盘扩容与「关机→立刻开机」生效

## 目的

新租的实例**不能**在创建时扩数据盘，只能租到之后再扩；而扩容提交后**不重启不生效**。
所以完整动作是「扩容提交 → 关机 → 立刻开机」。最后那一步必须零等待：
按量计费的稀缺机型关机后，GPU 随时会被别人抢走，晚一秒都可能开不回来。

实战基线：目标实例数据盘 **50GB → +100GB = 150GB**，页面回读「扩容后容量 150GB /
¥0.67 每日」。参数 `100` 是**新增容量**，不是目标总容量。

---

## 一、完整流程（`expand_data_disk.py`）

```
python expand_data_disk.py <实例ID> <新增GB>
   例：python expand_data_disk.py <实例ID> 100
```

| 步骤 | 页面动作 | 代码要点 |
|---|---|---|
| 1 | 展开实例行「更多」 | `hover` 后等 800ms，若下拉项没出现再 `click` |
| 2 | 点「扩容数据盘」 | `li.el-dropdown-menu__item:has-text('扩容数据盘')` |
| 3 | 运行中实例会先弹「需重启生效」提示 | `.el-message-box:visible` → 点「继续」 |
| 4 | 等真正的扩容对话框 | `.el-dialog:visible` + `.filter(has_text="扩容后容量")`，最多轮询 20×500ms |
| 5 | 填新增容量 | 输入框可能长期 `disabled`，见下节 JS 写入 |
| 6 | 回读确认 | 打印对话框里含「扩容 / 费用 / GB」的行，供人工核对 |
| 7 | 点「确定」提交 | 提交后表格会重渲染 |
| 8 | 关机 → 立刻开机 | `wait_shutdown_then_power_on`，见第三节 |

`:visible` 过滤不是可选项：页面里有 15 个隐藏 `.el-dialog`，
`.el-dialog:has-text('扩容').first` 会命中隐藏元素，`box.fill()` 白等 30 秒超时。

---

## 二、输入框 `disabled`：JS 强制启用 + Vue 事件派发

**概念先讲清楚**：这一步发生在「扩容对话框已可见、但 Element UI 还没把输入框初始化完」的时点。
`box.evaluate(script, str(gb))` 的第一个参数是在**页面上下文**里执行的函数，第二个参数是传给它的值
（新增 GB 的字符串）。它不返回业务结果，作用是**绕过 disabled 并把值写进 Vue 的响应式模型**；
随后 `page.wait_for_timeout(800)` 让页面重算，再 `dlg.inner_text()` 回读容量与费用——
回读才是真正的验证，写入本身不算成功。

```javascript
(el, val) => {
    el.disabled = false;
    el.removeAttribute('disabled');
    el.removeAttribute('aria-disabled');
    const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value'
    ).set;          // 必须用原生 setter，直接 el.value = 不会触发 Vue 的监听
    setter.call(el, val);
    el.dispatchEvent(new Event('input',  {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
}
```

为什么必须这样：Vue 2 靠 `input` 事件更新 `v-model`，Playwright 的 `locator.fill()`
在元素 `disabled` 时会一直等到超时；而直接改 `el.value` 又不会触发 Vue 的监听器，
页面上的「扩容后容量 / 费用」不会变，点「确定」提交的还是旧值（甚至 0）。
`input` + `change` 两个事件都派发、都 `bubbles: true`，是为了覆盖 Element UI
内部不同版本的监听方式。

**回读是硬要求**：脚本会把对话框文本里含「扩容 / 费用 / GB」的行打印出来。
实战回读为「扩容后容量 150GB / ¥0.67每日」才继续点「确定」。
若回读不对，宁可人工处理，也不要盲点确定。

---

## 三、`wait_shutdown_then_power_on`：零等待抢卡

**概念先讲清楚**：入参是 `page` 和 `target`（含 `id` / `row` 的字典），返回 `bool`。
它串起四段状态机，每一段**都按实例 ID 重新 `find_row()` 定位**——因为扩容提交后
Element UI 表格重渲染，之前传入的 `row` Locator 已经失效，复用旧引用会点不到按钮。

```
① 定位并点「关机」      轮询 15 次 × 2s（表格可能还在刷新）
② 可能的关机确认弹窗     .el-message-box:visible → 点「确定 / 确认」，无弹窗就跳过
③ 等状态变成「已关机」   60 次 × 1.5s（最多 90 秒）；超时则**不执行开机**，交人工
④ 状态一到「已关机」→ 立刻 find_row → 点「开机」→ 确认弹窗 → 等回到非关机状态
```

关键设计：

- ③ 和 ④ 之间**没有任何 `sleep`**。这是用户反复强调的「关机之后需要立刻开机，否则抢不到」。
- ③ 超时时明确**放弃开机**并打印当前状态，避免在不确定的状态下乱点。
- 「关机」「开机」都用 `find_row_button(row, exact_text)` 遍历行内按钮做精确文本匹配，
  绝不会误点「设置定时关机」。
- 成功 / 失败都写 `instances_log.json`（`event=power_cycle_after_expand`，带 `ok` 字段），
  便于事后判断「扩容到底生效没有」。

---

## 四、二次扣费陷阱（最容易犯的错）

> **扩容提交与重启是两步。** 如果 `expand_data_disk.py` 已经点了「确定」（扩容已提交），
> 但后面的关机/开机失败，**绝对不要重跑整条命令**——会再扩一次、再扣一次费。

正确做法是只补重启那半步：

```bash
# 一次性：仅对指定实例执行「关机 → 立刻开机」，不碰任何扩容入口
python power_cycle_once.py
```

`power_cycle_once.py` 内部直接调 `wait_shutdown_then_power_on`，
`TARGET_ID` 是脚本顶部的常量（换实例要改）。它存在的唯一理由就是把这个陷阱结构化地避开。

判断该用哪个：

| 现象 | 该跑什么 |
|---|---|
| 还没扩容 | `expand_data_disk.py <实例ID> <新增GB>`（一次跑完两步） |
| 扩容已提交、日志里 `power_cycle_after_expand ok=false` | `power_cycle_once.py` |
| 扩容已提交、日志里 `ok=true` | 什么都不用跑，进实例确认容量即可 |

---

## 五、实例元数据日志

`instance_log.record_event(event, data)` 把每次动作追加到 `instances_log.json`：
`time` / `event` / `id` / `region` / `status` / `by` 等，**只记元数据，不含账号凭证**。
已知事件类型：`rent_created`、`data_disk_expanded`、`power_cycle_after_expand`、`power_on`、
关机事件。用途：

1. 事后追溯「机器人到底动过哪些机器」；
2. 配合 `shutdown_instance.py <ID前缀>` 精确关掉某一台；
3. 判断扩容/重启是否成功，决定要不要补跑 `power_cycle_once.py`。

`shutdown_instance.py` 不带参数时是**只读列出**当前所有实例（ID / 状态 / 距释放时间），
带 ID（支持前缀）才执行关机，且需要终端输入 `yes` 确认。关机即停止计费、数据保留
（按量计费实例关机后保留约 15 天）。

---

## 六、复用前必读：怎么吸收这些代码

| 维度 | 内容 |
|---|---|
| **全盘吸收** | `:visible` + `has_text` 双重过滤、原生 setter + `input`/`change` 双事件派发、填完必须回读、每步按 ID 重新定位行、关机→开机零等待、扩容/重启拆成两个可独立执行的脚本。 |
| **看业务调整** | 新增 GB 数、`TARGET_ID`、轮询次数与超时（表格刷新慢的机器可放宽）、回读时匹配的关键词。 |
| **该抛弃** | 脚本里写死的实例 ID、为单次实战记的容量数字（50→150GB）、`screenshots/` 里的证据图。 |
| **埋的雷** | **重跑整条扩容命令 = 二次扣费**；`disabled` 输入框若只改 `value` 不派发事件，提交的是旧值；关机超时后盲目点开机可能点到别的实例行；`instances_log.json` 含真实实例 ID，不要提交到公开仓库。 |

---

## 附录：排错

- **扩容对话框一直不出现** → 先看有没有「需重启生效」提示框没点「继续」；
  再确认 `.el-dialog:visible` 过滤是否命中了隐藏面板。
- **`box.fill()` 等 30 秒超时** → 输入框 `disabled` 未解除，走第二节的 JS 写入。
- **点「确定」后表格里的按钮点不动** → 表格重渲染，旧 `row` 引用失效，按 ID 重新定位。
- **关机后开不回来** → 主机 GPU 被别人抢了。此时不要反复硬点，
  改用 `power_on_poll.py` 做低风险长时间轮询（见 `zero-login-and-anti-ban.md`）。
- **扩容到底生效没有** → 进实例 `df -h` 看数据盘；同时核对 `instances_log.json`
  里 `power_cycle_after_expand` 的 `ok` 是否为 `true`。
