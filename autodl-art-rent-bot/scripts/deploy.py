"""创建应用实例（会真的花钱）。

默认 dry-run：只打印将要提交的 payload，不发请求。
加 --yes 才真正调用 create 接口。

这是整条链路里唯一会产生费用的动作，所以设计成：
  1. 默认不动；
  2. 必须显式 --yes；
  3. 执行前回显完整参数，人工可核对；
  4. 不做任何自动重试（避免重复建实例）。

用法：
    python deploy.py --app-id 273 --gpu 5090-p --gpu-amount 1 --dry-run
    python deploy.py --app-id 273 --gpu 5090-p --gpu-amount 1 --yes
"""

import argparse
import json
import sys

import config
import dev_api


def load_app_details():
    path = config.ensure_output_dir() / "app_details.json"
    if not path.exists():
        raise SystemExit("缺少 outputs/app_details.json，先跑 fetch_details.py")
    return json.loads(path.read_text(encoding="utf-8"))["apps"]


def resolve_app(app_id, details):
    item = details.get(str(app_id))
    if not item:
        raise SystemExit("app_details.json 里没有 id=%s，先把该应用详情抓下来" % app_id)
    uuid = item.get("uuid")
    if not uuid:
        raise SystemExit("该应用详情里没有 uuid，无法确定 application_uuid")
    return uuid, item


def main():
    ap = argparse.ArgumentParser(description="创建 AutoDL Art 应用实例（默认 dry-run）")
    ap.add_argument("--app-id", required=True, help="应用 id（outputs/app_details.json 的键）")
    ap.add_argument("--gpu", required=True, help="算力规格 ID，见 select_gpu.py")
    ap.add_argument("--gpu-amount", type=int, default=1, help="GPU 数量，1-4")
    ap.add_argument("--disk", type=int, default=0, help="系统盘扩容 GB，0-500")
    ap.add_argument("--version", default="latest", help="应用版本，默认 latest")
    ap.add_argument("--name", default="", help="实例备注名")
    ap.add_argument("--region", default="", help="地区，如 westDC3 / beijingDC2，留空由系统选")
    ap.add_argument("--yes", action="store_true", help="真正执行创建（会花钱）；不加则只 dry-run")
    ap.add_argument("--dry-run", action="store_true", help="显式 dry-run（与默认行为一致，便于照文档跑）")
    args = ap.parse_args()

    if args.yes and args.dry_run:
        raise SystemExit("--yes 和 --dry-run 不能同时加")

    valid_specs = {s[0] for s in config.GPU_SPECS}
    if args.gpu not in valid_specs:
        raise SystemExit("非法规格 ID：%s；可选：%s" % (args.gpu, ", ".join(sorted(valid_specs))))
    if not 1 <= args.gpu_amount <= 4:
        raise SystemExit("--gpu-amount 必须在 1-4")
    if not 0 <= args.disk <= 500:
        raise SystemExit("--disk 必须在 0-500")

    details = load_app_details()
    app_uuid, item = resolve_app(args.app_id, details)

    payload = {
        "req_gpu_amount": args.gpu_amount,
        "expand_system_disk_by_gb": args.disk,
        "gpu_spec_uuid": args.gpu,
        "application_uuid": app_uuid,
        "application_version": args.version,
        "start_command": "sleep 1",
    }
    if args.name:
        payload["instance_name"] = args.name
    if args.region:
        payload["data_center_list"] = [args.region]

    print("应用：%s（id=%s，uuid=%s，最新版本 %s）" % (
        item.get("name"), args.app_id, app_uuid, item.get("latest_version")))
    print("将要提交的 payload：")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if not args.yes:
        print()
        print("这是 dry-run，没有发请求、没有花钱。")
        print("确认上面的参数无误后，加 --yes 再跑一次才会真正创建。")
        return 0

    print()
    print("开始创建实例（按量计费，会立即产生费用）……")
    try:
        instance_uuid = dev_api.create(
            gpu_spec_uuid=args.gpu,
            application_uuid=app_uuid,
            application_version=args.version,
            req_gpu_amount=args.gpu_amount,
            expand_system_disk_by_gb=args.disk,
            instance_name=args.name,
            data_center_list=[args.region] if args.region else None,
        )
    except dev_api.DevApiError as e:
        print("创建失败：%s" % e)
        return 2

    print("创建成功，实例 ID：%s" % instance_uuid)
    print("下一步可查看状态：python dev_check.py，或 python power_ctl.py %s status" % instance_uuid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
