import os, re, glob, datetime

# Chrome 用户数据根目录：按当前 Windows 用户推导，不写死本机用户名
root = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Google", "Chrome", "User Data")

profiles = []
if os.path.isdir(os.path.join(root, "Default")):
    profiles.append("Default")
for d in sorted(glob.glob(os.path.join(root, "Profile *"))):
    if os.path.isdir(d):
        profiles.append(os.path.basename(d))

print("发现的 profile:", profiles)
today = datetime.datetime.now().strftime("%Y%m%d")
print("今天日期:", today)
print("=" * 60)

for prof in profiles:
    ls = os.path.join(root, prof, "Local Storage", "leveldb")
    if not os.path.isdir(ls):
        print(f"[{prof}] 无 Local Storage")
        continue
    blob = b""
    files = glob.glob(os.path.join(ls, "*.log")) + glob.glob(os.path.join(ls, "*.ldb"))
    read_ok = 0
    for f in files:
        try:
            blob += open(f, "rb").read()
            read_ok += 1
        except OSError:
            pass
    text = blob.decode("utf-8", "ignore")
    has_autodl = "autodl.com" in text.lower()
    # 所有 login_time 时间戳（14位）
    times = sorted(set(re.findall(r'login_time"?\s*[:=]\s*"?(\d{14})', text)))
    latest = times[-1] if times else None
    fresh = bool(latest and latest.startswith(today))
    # token key（形如 _l_xxxxx）与其后的值
    tok = re.findall(r'(_l_[A-Za-z0-9+/]{3,}[+\-]?)\s*([A-Za-z0-9]{16,})', text)
    tok_info = tok[-1] if tok else None
    print(f"[{prof}] 读取文件 {read_ok}/{len(files)} 个 | 含autodl={has_autodl}")
    print(f"    login_time列表(末3个)={times[-3:]} 最新={latest} 是否今天={fresh}")
    if tok_info:
        print(f"    令牌key={tok_info[0]} 值={tok_info[1][:12]}...(长度{len(tok_info[1])})")
