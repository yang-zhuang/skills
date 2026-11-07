import os, re, glob
from pathlib import Path

# 副本目录 = 本脚本同级的 chrome_profile/，按脚本位置推导
COPY_DEFAULT = Path(__file__).parent / "chrome_profile" / "Default"
base = str(COPY_DEFAULT / "Local Storage" / "leveldb")
print("leveldb 目录存在:", os.path.isdir(base))
if not os.path.isdir(base):
    base = str(COPY_DEFAULT / "Local Storage")

# 扫描 leveldb 文件里可读字符串中与 autodl / token 相关的内容
pat = re.compile(r"[A-Za-z0-9_\-\.:/]{4,}")
hits = {}
for f in glob.glob(os.path.join(base, "**", "*.*"), recursive=True):
    if os.path.splitext(f)[1] not in (".log", ".ldb", ".txt", ""):
        continue
    try:
        data = open(f, "rb").read()
    except OSError:
        continue
    text = data.decode("utf-8", "ignore")
    # 找 autodl.com 的 origin key 及附近可能的 token 字段名
    if "autodl" in text.lower():
        hits.setdefault(f, []).append("contains 'autodl'")
    for kw in ("token", "Token", "Authorization", "user", "login"):
        if kw in text and "autodl" in text.lower():
            hits.setdefault(f, []).append(f"kw={kw}")

if not hits:
    print("localStorage 中未发现 autodl 相关数据")
else:
    for f, kws in hits.items():
        print(f, "->", sorted(set(kws)))

# 另外直接打印包含 autodl 的可读片段（前若干）
shown = 0
for f in glob.glob(os.path.join(base, "**", "*.log"), recursive=True) + glob.glob(
    os.path.join(base, "**", "*.ldb"), recursive=True
):
    try:
        data = open(f, "rb").read().decode("utf-8", "ignore")
    except OSError:
        continue
    for m in re.finditer(r".{0,40}autodl.{0,80}", data, re.IGNORECASE):
        seg = m.group(0).replace("\x00", "")
        print("FRAG:", seg[:120])
        shown += 1
        if shown >= 15:
            break
    if shown >= 15:
        break
