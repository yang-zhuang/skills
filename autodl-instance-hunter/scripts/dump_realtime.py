import os, re, glob

# 真实 Chrome 的 leveldb：按当前 Windows 用户推导，不写死本机用户名
ls = os.path.join(
    os.path.expanduser("~"), "AppData", "Local", "Google", "Chrome",
    "User Data", "Default", "Local Storage", "leveldb",
)
files = glob.glob(os.path.join(ls, "*.log")) + glob.glob(os.path.join(ls, "*.ldb"))
print("leveldb 文件:", [os.path.basename(f) for f in files])

blob = b""
for f in files:
    try:
        blob += open(f, "rb").read()
    except OSError as e:
        print("读取失败", f, e)

text = blob.decode("utf-8", "ignore")
print("总长度:", len(text))
print("=" * 60)

# 打印含 autodl 的片段
shown = 0
for m in re.finditer(r".{0,10}autodl.{0,120}", text, re.IGNORECASE):
    seg = m.group(0).replace("\x00", "")
    print("FRAG:", seg[:150])
    shown += 1
    if shown >= 25:
        break

print("=" * 60)
# 打印所有 14 位数字（可能是 login_time 之类）
nums = sorted(set(re.findall(r'(?<!\d)(20\d{12})(?!\d)', text)))
print("14位时间戳(候选):", nums[-10:])

# 打印可能的令牌（长 base64 串）
toks = re.findall(r'[A-Za-z0-9]{30,60}', text)
uniq = []
for t in toks:
    if t not in uniq:
        uniq.append(t)
print("长字符串候选(前15):")
for t in uniq[:15]:
    print("  ", t)
