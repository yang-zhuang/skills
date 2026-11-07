"""本地验证：型号归一化 + 优先级列表 + 排序规则。不启动浏览器。"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

import config

print("=== 1) 归一化测试 ===")
cases = [
    ("VGPU-48g-350w", "vGPU-48GB-350W"),
    ("VGPU-32G", "vGPU-32GB"),
    ("vgpu-32gb", "vGPU-32GB"),
    ("vGPU-48GB", "vGPU-48GB"),
    ("vGPU-48GB-350W", "vGPU-48GB-350W"),
]
all_ok = True
for raw, expect in cases:
    got = config.normalize_model(raw)
    ok = got == expect
    all_ok = all_ok and ok
    print(f"  {raw!r:24} -> {got!r:20} {'OK' if ok else 'FAIL(期望 ' + expect + ')'}")

print("=== 2) .env 加载的优先级列表 ===")
print(f"  GPU_MODEL 原始串: {config.GPU_MODEL}")
print(f"  GPU_MODELS 优先级: {config.GPU_MODELS}")
ok2 = config.GPU_MODELS == ["vGPU-48GB-350W", "vGPU-32GB"]
print(f"  {'OK' if ok2 else 'FAIL: 期望 [vGPU-48GB-350W, vGPU-32GB]'}")

print("=== 3) 前缀安全（350W 绝不能与 48GB 混淆）===")
safe = config.normalize_model("vGPU-48GB") != config.normalize_model("vGPU-48GB-350W")
print(f"  vGPU-48GB != vGPU-48GB-350W : {'OK' if safe else 'FAIL'}")
all_ok = all_ok and ok2 and safe

print("=== 4) 排序规则（内存↓，再可扩容↓）===")
# 模拟 collect_rentable_machines 的排序键
machines = [
    {"gpu_name": "机A", "memory_gb": 110, "expandable_gb": 0},
    {"gpu_name": "机B", "memory_gb": 110, "expandable_gb": 103},
    {"gpu_name": "机C", "memory_gb": 72, "expandable_gb": 300},
    {"gpu_name": "机D", "memory_gb": 120, "expandable_gb": 50},
]
machines.sort(key=lambda m: (m["memory_gb"], m["expandable_gb"]), reverse=True)
order = [m["gpu_name"] for m in machines]
print(f"  排序结果: {order}")
# 期望: D(120) > B(110,103) > A(110,0) > C(72,300 虽可扩容大但内存小)
expect_order = ["机D", "机B", "机A", "机C"]
ok3 = order == expect_order
print(f"  {'OK' if ok3 else 'FAIL: 期望 ' + str(expect_order)}")
all_ok = all_ok and ok3

print()
print("ALL_PASS" if all_ok else "SOME_FAILED")
sys.exit(0 if all_ok else 1)
