"""实例状态查看与开关机/释放。

用法：
    python power_ctl.py list
    python power_ctl.py status <instance_uuid>
    python power_ctl.py snapshot <instance_uuid>
    python power_ctl.py on <instance_uuid>
    python power_ctl.py off <instance_uuid>
    python power_ctl.py release <instance_uuid> --yes

release 不可逆（实例连同数据一起销毁），所以必须显式 --yes。
on/off 是低成本状态切换，但仍会真实作用到账号，别循环打。
"""

import argparse
import json
import sys

import dev_api


def print_result(label, data):
    print("%s：%s" % (label, json.dumps(data, ensure_ascii=False)))


def main():
    ap = argparse.ArgumentParser(description="AutoDL Art 实例状态与开关机")
    ap.add_argument("action", choices=["list", "status", "snapshot", "on", "off", "release"])
    ap.add_argument("instance_uuid", nargs="?", help="实例 ID，list 时可省略")
    ap.add_argument("--yes", action="store_true", help="release 时必须加，确认销毁")
    args = ap.parse_args()

    try:
        if args.action == "list":
            data = dev_api.list_instances(page_size=50) or {}
            print("共 %s 个实例" % data.get("result_total"))
            for it in data.get("list") or []:
                print("- %s | %s | %s | %s卡 | %s" % (
                    it.get("uuid"), it.get("status"), it.get("region_name") or it.get("region_sign"),
                    it.get("req_gpu_amount"), it.get("gpu_spec_uuid")))
            return 0

        if not args.instance_uuid:
            raise SystemExit("%s 需要 instance_uuid" % args.action)

        if args.action == "status":
            print_result("状态", dev_api.status(args.instance_uuid))
        elif args.action == "snapshot":
            print_result("详情", dev_api.snapshot(args.instance_uuid))
        elif args.action == "on":
            print_result("开机", dev_api.power_on(args.instance_uuid))
        elif args.action == "off":
            print_result("关机", dev_api.power_off(args.instance_uuid))
        elif args.action == "release":
            if not args.yes:
                raise SystemExit("release 会销毁实例和数据，确认无误后加 --yes")
            print_result("释放", dev_api.release(args.instance_uuid))
        return 0
    except dev_api.DevApiError as e:
        print("失败：%s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
