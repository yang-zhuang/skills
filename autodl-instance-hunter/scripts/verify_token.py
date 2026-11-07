import os, re, glob, base64, json, datetime

def b64pad(s):
    return s + "=" * (-len(s) % 4)

def scan_leveldb(ls):
    files = glob.glob(os.path.join(ls, "*.log")) + glob.glob(os.path.join(ls, "*.ldb"))
    blob = b""
    for f in files:
        try:
            blob += open(f, "rb").read()
        except OSError:
            pass
    text = blob.decode("utf-8", "ignore")
    return text, len(files)

def jwt_info(text):
    # 找 token 键后面的 JWT（eyJ 开头）
    m = re.search(r'token\x01?(eyJ[A-Za-z0-9_\-]+)\.([A-Za-z0-9_\-]+)\.([A-Za-z0-9_\-]*)', text)
    if not m:
        # 兜底：直接找 JWT
        m = re.search(r'(eyJ[A-Za-z0-9_\-]{10,})\.([A-Za-z0-9_\-]+)\.([A-Za-z0-9_\-]*)', text)
    if not m:
        return None
    payload_b64 = m.group(2)
    try:
        payload = json.loads(base64.urlsafe_b64decode(b64pad(payload_b64)))
    except Exception:
        return {"raw_header": m.group(1)[:20]}
    return payload

now = int(datetime.datetime.now().timestamp())
# 副本路径按脚本位置推导；真实 Chrome 路径按当前用户推导，均不写死本机信息
COPY_LS = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "chrome_profile",
    "Default", "Local Storage", "leveldb",
)
REAL_LS = os.path.join(
    os.path.expanduser("~"), "AppData", "Local", "Google", "Chrome",
    "User Data", "Default", "Local Storage", "leveldb",
)
for label, ls in [
    ("副本", COPY_LS),
    ("真实", REAL_LS),
]:
    text, nfiles = scan_leveldb(ls)
    info = jwt_info(text)
    print(f"[{label}] leveldb文件数={nfiles}")
    if not info:
        print("   未找到 JWT token")
        continue
    iat = info.get("iat")
    exp = info.get("exp")
    if iat:
        print(f"   uid={info.get('uid')} iat={datetime.datetime.fromtimestamp(iat)} exp={datetime.datetime.fromtimestamp(exp)}")
        print(f"   是否有效(exp>now)={exp > now} 是否今天签发={datetime.datetime.fromtimestamp(iat).strftime('%Y%m%d')==datetime.datetime.now().strftime('%Y%m%d')}")
    else:
        print("   ", info)
