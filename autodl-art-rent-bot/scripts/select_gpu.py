"""列出可选的 GPU 算力规格，并说明为什么这里看不到库存。

官方开发者 API 只给了 6 个固定的算力规格 ID，**没有**查询某型号实时库存/价格的端点。
实时库存只能去网页部署页（`https://www.autodl.art/app/deploy?...`）上看。
本脚本负责把「规格 ID ↔ 网页显示名 ↔ 类型」对齐，避免填错。

用法：
    python select_gpu.py
"""

import config


def main():
    print("GPU 算力规格（官方文档硬编码，共 %d 个）：" % len(config.GPU_SPECS))
    print()
    print("%-14s %-14s %-8s" % ("规格ID(填API)", "网页显示名", "类型"))
    print("-" * 40)
    for spec_id, display, tier in config.GPU_SPECS:
        print("%-14s %-14s %-8s" % (spec_id, display, tier))
    print()
    print("注意：本 API 没有库存/价格查询端点。")
    print("  实时库存、按量价格请到网页部署页确认：")
    print("  https://www.autodl.art/app/market  → 打开应用 → 选卡")
    print()
    print("选定后传给 deploy.py：")
    print("  python deploy.py --app-id 273 --gpu 5090-p --gpu-amount 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
