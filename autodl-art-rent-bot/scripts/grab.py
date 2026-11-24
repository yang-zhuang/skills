"""抢开机 / 抢创建：反复尝试直到成功，效果对齐 autodl-rent-bot 的"蹲点抢卡"。

三种模式：
    stock <地区,...>                    查库存（只读，需网页 JWT）
    power  <instance_uuid>              对已关机实例反复调 power_on，成功且状态变 running 即停
    create --app-id N --gpu <spec,...>  反复调 create，成功建出实例即停

    create 的 --gpu 支持逗号分隔多个规格（即"给定规则"）：
        --gpu 5090-p,pro6000-p  表示每次尝试轮换规格，任何一个先建成功就停。
    create 加 --signal 后进入"有货才点"模式（对齐 autodl 的 .gpuTips 逻辑）：
        每轮先查 machine_pro/gpu_type 库存（gpu_idle_num），有空闲卡才发 create，
        无货只等待不发请求。需要网页 JWT：先跑一次 `python extract_jwt.py --login`。

和 autodl-rent-bot 的差别：
    信号模式下几乎无差别：都是"有货才点"，无效请求趋近于零。
    唯一前提是 extract_jwt.py 登录过一次（之后 profile 常驻，全自动）。
    不用 --signal 则为盲试模式，间隔默认 60 秒 + 随机抖动，下限 30 秒。

防风控硬规则（写死在代码里，不可关）：
    1. 永远单线程、单账号、串行请求，绝不并发；
    2. 每次请求之间有 dev_api._throttle() 强制 ≥1 秒间隔，轮询间隔默认 60 秒 ±25% 抖动；
    3. 致命错误（token/JWT 无效 / 参数错 / 余额不足）立刻停，绝不带着坏状态硬抡；
    4. 连续失败 ≥ 15 次自动退避：等待时间逐次 ×1.5，最多放大到基础间隔的 4 倍，
       成功或错误类型变化即重置 —— 失败越多越安静，绝不加速重试；
    5. create 每次失败后复查实例列表，防止"超时但其实建成功了"被重复创建；
    6. 成功立即停；到达 --hours 上限自动停；Ctrl+C 随时停。
    7. 登录方式：extract_jwt.py 登录一次后，JWT 提取全自动；脚本从不碰登录表单、验证码、密码。

用法：
    python extract_jwt.py --login                                           # 第一次：登录一次
    python grab.py stock 西北B区,北京B区                                     # 看库存（只读）
    python grab.py power pro-xxxx --interval 60 --hours 12 --dry-run
    python grab.py power pro-xxxx --interval 60 --hours 12 --yes
    python grab.py create --app-id 273 --gpu 5090-p --interval 60 --hours 12 --yes
    python grab.py create --app-id 273 --gpu 5090-p,pro6000-p --region 西北B区 --signal --hours 12 --yes
"""

import argparse
import json
import random
import sys
import time
from datetime import datetime

import config
import dev_api
import web_api


def now():
    return datetime.now().strftime("%H:%M:%S")


def make_logger(name):
    out_dir = config.ensure_output_dir()
    path = out_dir / ("grab_%s_%s.log" % (name, datetime.now().strftime("%Y%m%d_%H%M%S")))
    fh = open(path, "w", encoding="utf-8")

    def log(msg):
        line = "[%s] %s" % (now(), msg)
        print(line, flush=True)
        fh.write(line + "\n")
        fh.flush()

    return log, path


def jitter(interval):
    """在 interval 上做 ±25% 抖动再加 0~5 秒，避免固定节奏。"""
    base = max(30, interval)
    return base * random.uniform(0.75, 1.25) + random.uniform(0, 5)


def backoff_wait(interval, consecutive_failures):
    """防风控退避：连续失败越多，等待越长（最多放大到 4 倍基础间隔）。

    与 jitter 不同：jitter 是每次正常轮询的抖动；backoff 是连续失败后的额外放大。
    """
    factor = 1.0
    if consecutive_failures >= 15:
        factor = min(1.5 ** (consecutive_failures - 14), 4.0)
    return min(jitter(interval) * factor, max(30, interval) * 4 + 5)


def handle_fatal(e, log):
    """致命错误处理：打印并退出非 0，绝不重试。"""
    log("致命错误，立刻停止（重试无意义且危险）：%s" % e)
    return 3


def spec_display(spec_id):
    """规格 ID → 网页显示名（如 5090-p → 5090-32G）；库存接口返回的是显示名。"""
    for sid, display, _ in config.GPU_SPECS:
        if sid == spec_id:
            return display
    return spec_id


def query_stock(regions, log):
    """查询库存，返回 {显示名: 空闲数}；网页 JWT 失效会抛 FatalDevApiError。"""
    rows = web_api.stock(regions)
    return {r["gpu_type"]: (r["idle"] or 0) for r in rows}, rows


def human_left(seconds):
    if seconds <= 0:
        return "已到最后时限"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return "%d 小时 %d 分" % (h, m) if h else "%d 分 %d 秒" % (m, s)


# ------------------------- power 模式 -------------------------

def run_power(uuid, interval, hours, execute, log):
    deadline = time.monotonic() + hours * 3600
    log("目标实例：%s；间隔约 %d 秒；最长 %.1f 小时" % (uuid, interval, hours))

    try:
        st = dev_api.status(uuid)
        log("当前状态：%s" % st)
        if st == "running":
            log("已经是 running，不需要抢。")
            return 0
    except dev_api.DevApiError as e:
        log("查状态失败（继续尝试）：%s" % e)

    if not execute:
        log("dry-run：没有发开机请求。确认后加 --yes 再跑。")
        return 0

    attempt = 0
    consecutive_failures = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            dev_api.power_on(uuid)
            log("第 %d 次：power_on 返回 Success" % attempt)
            # 成功后确认状态；有些情况 Success 只是受理，要再等
            time.sleep(5)
            st = dev_api.status(uuid)
            log("第 %d 次：开机后状态 = %s" % (attempt, st))
            if st in ("running", "starting", "开机中"):
                log("抢到开机，结束。")
                return 0
            consecutive_failures = 0
        except dev_api.FatalDevApiError as e:
            return handle_fatal(e, log)
        except dev_api.DevApiError as e:
            consecutive_failures += 1
            log("第 %d 次失败（连续第 %d 次）：%s" % (attempt, consecutive_failures, e))

        left = deadline - time.monotonic()
        if left <= 0:
            break
        wait = min(backoff_wait(interval, consecutive_failures), left)
        log("等待 %.0f 秒后重试（剩余 %s）" % (wait, human_left(left)))
        time.sleep(wait)

    log("到达时间上限，共尝试 %d 次，未抢到。" % attempt)
    return 1


# ------------------------- create 模式 -------------------------

def list_uuids():
    data = dev_api.list_instances(page_size=50) or {}
    return {it.get("uuid") for it in (data.get("list") or [])}


def run_create(app_id, gpus, gpu_amount, disk, version, region, interval, hours, execute, log,
               signal=False, regions=None):
    details_path = config.ensure_output_dir() / "app_details.json"
    if not details_path.exists():
        raise SystemExit("缺少 outputs/app_details.json，先跑 fetch_details.py")
    item = json.loads(details_path.read_text(encoding="utf-8"))["apps"].get(str(app_id))
    if not item or not item.get("uuid"):
        raise SystemExit("app_details.json 里没有 id=%s 或缺少 uuid" % app_id)
    app_uuid = item["uuid"]

    log("应用：%s（id=%s，uuid=%s）" % (item.get("name"), app_id, app_uuid))
    log("规则：规格 %s × %d 卡轮换尝试；间隔约 %d 秒；最长 %.1f 小时；%s"
        % ("|".join(gpus), gpu_amount, interval, hours,
           "有库存信号才点（对齐 autodl）" if signal else "盲试（无信号模式）"))

    if not execute:
        log("dry-run：没有发创建请求。确认后加 --yes 再跑。")
        return 0

    baseline = list_uuids()
    log("开始前已有 %d 个实例，作为基线" % len(baseline))

    deadline = time.monotonic() + hours * 3600
    attempt = 0
    consecutive_failures = 0
    while time.monotonic() < deadline:
        attempt += 1
        gpu = gpus[attempt % len(gpus)]  # 默认按规则轮换

        # ---- 信号模式：先查库存，有货才点（对齐 autodl-rent-bot 的 .gpuTips 逻辑）----
        if signal:
            try:
                idle_map, rows = query_stock(regions, log)
                if attempt == 1 or attempt % 20 == 0:
                    log("当前库存：%s" % ", ".join(
                        "%s(%d/%s)" % (r["gpu_type"], r["idle"], r["total"]) for r in rows))
                available = [g for g in gpus if idle_map.get(spec_display(g), 0) >= gpu_amount]
                if not available:
                    log("第 %d 轮：%s 在 %s 均无货（空闲<%d），本轮不发 create。"
                        % (attempt, ",".join(spec_display(g) for g in gpus),
                           "+".join(regions), gpu_amount))
                    left = deadline - time.monotonic()
                    if left <= 0:
                        break
                    wait = min(backoff_wait(interval, 0), left)  # 无货不算失败，不退避
                    log("等待 %.0f 秒后重新查库存（剩余 %s）" % (wait, human_left(left)))
                    time.sleep(wait)
                    continue
                gpu = available[0]
                log("第 %d 轮：%s 有货（空闲 %d），发 create。"
                    % (attempt, spec_display(gpu), idle_map[spec_display(gpu)]))
            except dev_api.FatalDevApiError as e:
                return handle_fatal(e, log)
            except dev_api.DevApiError as e:
                log("第 %d 轮：查库存失败（本轮退回盲试）：%s" % (attempt, e))

        try:
            new_uuid = dev_api.create(
                gpu_spec_uuid=gpu,
                application_uuid=app_uuid,
                application_version=version,
                req_gpu_amount=gpu_amount,
                expand_system_disk_by_gb=disk,
                data_center_list=[regions[0]] if signal and regions else ([region] if region else None),
            )
            log("第 %d 次：创建成功（规格 %s），实例 %s" % (attempt, gpu, new_uuid))
            return 0
        except dev_api.FatalDevApiError as e:
            return handle_fatal(e, log)
        except dev_api.DevApiError as e:
            consecutive_failures += 1
            log("第 %d 次失败（规格 %s，连续第 %d 次）：%s" % (attempt, gpu, consecutive_failures, e))
            # 关键防重：超时/网络错误可能实际已建成，先比对列表，避免重复创建
            try:
                current = list_uuids()
                fresh = current - baseline
                if fresh:
                    log("发现新实例 %s —— 说明上次其实建成功了，停止，避免重复计费。" % ", ".join(sorted(fresh)))
                    return 0
            except dev_api.FatalDevApiError as e2:
                return handle_fatal(e2, log)
            except dev_api.DevApiError as e2:
                log("复查列表也失败：%s" % e2)

        left = deadline - time.monotonic()
        if left <= 0:
            break
        wait = min(backoff_wait(interval, consecutive_failures), left)
        log("等待 %.0f 秒后重试（剩余 %s）" % (wait, human_left(left)))
        time.sleep(wait)

    log("到达时间上限，共尝试 %d 次，未抢到。" % attempt)
    return 1


def main():
    ap = argparse.ArgumentParser(description="抢开机 / 抢创建（默认 dry-run，防风控退避内置）")
    sub = ap.add_subparsers(dest="mode", required=True)

    p = sub.add_parser("power", help="对已关机实例反复尝试开机")
    p.add_argument("instance_uuid")
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--hours", type=float, default=12)
    p.add_argument("--yes", action="store_true")
    p.add_argument("--dry-run", action="store_true")

    c = sub.add_parser("create", help="反复尝试创建实例")
    c.add_argument("--app-id", required=True)
    c.add_argument("--gpu", required=True,
                   help="规格 ID，逗号分隔可配多个轮换，如 5090-p,pro6000-p")
    c.add_argument("--gpu-amount", type=int, default=1)
    c.add_argument("--disk", type=int, default=0)
    c.add_argument("--version", default="latest")
    c.add_argument("--region", default="")
    c.add_argument("--signal", action="store_true",
                   help="有库存信号才点（需先跑 extract_jwt.py 登录一次；--region 必填，逗号分隔多地区）")
    c.add_argument("--interval", type=int, default=60)
    c.add_argument("--hours", type=float, default=12)
    c.add_argument("--yes", action="store_true")
    c.add_argument("--dry-run", action="store_true")

    s = sub.add_parser("stock", help="查各地 GPU 库存（只读；需 extract_jwt.py 已提取网页 JWT）")
    s.add_argument("regions", help="地区名，逗号分隔，如：西北B区,北京B区")

    sub.add_parser("list", help="列出全部实例和状态（只读，方便挑关机的机器）")

    args = ap.parse_args()

    if args.mode == "stock":
        regions = [r.strip() for r in args.regions.split(",") if r.strip()]
        try:
            rows = web_api.stock(regions)
        except dev_api.FatalDevApiError as e:
            print("无法查询：%s" % e)
            return 3
        print("地区：%s" % " + ".join(regions))
        for r in rows:
            mark = "✅" if (r["idle"] or 0) > 0 else "·"
            print("  %s %-14s 空闲 %3d / 总数 %3d" % (mark, r["gpu_type"], r["idle"], r["total"]))
        return 0

    if args.mode == "list":
        data = dev_api.list_instances(page_size=50) or {}
        items = data.get("list") or []
        print("实例总数：%d" % len(items))
        for it in items:
            print("  %s  %-10s  %s" % (it.get("uuid"), it.get("status"),
                                       (it.get("name") or "")[:40]))
        return 0

    if args.yes and args.dry_run:
        raise SystemExit("--yes 和 --dry-run 不能同时加")
    execute = args.yes

    if args.mode == "power":
        log, path = make_logger("power_%s" % args.instance_uuid)
        log("日志：%s" % path)
        code = run_power(args.instance_uuid, args.interval, args.hours, execute, log)
    else:
        valid = {s[0] for s in config.GPU_SPECS}
        gpus = [g.strip() for g in args.gpu.split(",") if g.strip()]
        bad = [g for g in gpus if g not in valid]
        if bad:
            raise SystemExit("非法规格 ID：%s；可选：%s" % (", ".join(bad), ", ".join(sorted(valid))))
        log, path = make_logger("create_%s" % args.app_id)
        log("日志：%s" % path)
        regions = None
        if args.signal:
            regions = [r.strip() for r in args.region.split(",") if r.strip()]
            if not regions:
                raise SystemExit("--signal 需要同时给 --region（库存按地区查），如 --region 西北B区,北京B区")
            if args.signal and execute and not config.WEB_JWT:
                raise SystemExit("--signal 需要网页 JWT：先跑一次 `python extract_jwt.py --login`（登录一次后全自动）")
        code = run_create(args.app_id, gpus, args.gpu_amount, args.disk,
                          args.version, args.region, args.interval, args.hours, execute, log,
                          signal=args.signal, regions=regions)
    return code


if __name__ == "__main__":
    sys.exit(main())
