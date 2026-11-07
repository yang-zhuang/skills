import sqlite3, os, datetime
from pathlib import Path

# 检查 profile 副本里的 autodl cookie 名称与过期时间
# 副本目录 = 本脚本同级的 chrome_profile/，按脚本位置推导，可移植到任意 clone 目录
COPY_DEFAULT = Path(__file__).parent / "chrome_profile" / "Default"
db = str(COPY_DEFAULT / "Network" / "Cookies")
if not os.path.exists(db):
    db = str(COPY_DEFAULT / "Cookies")
print("DB:", db)

WEBKIT_EPOCH_OFFSET = 11644473600  # 1601->1970 秒
now = int(datetime.datetime.now().timestamp())

con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
cur = con.cursor()
cur.execute(
    "SELECT host_key, name, expires_utc, is_secure, is_httponly "
    "FROM cookies WHERE host_key LIKE '%autodl%' ORDER BY name"
)
rows = cur.fetchall()
con.close()

print(f"共 {len(rows)} 个 autodl cookie：")
for host, name, exp_utc, secure, httponly in rows:
    if exp_utc == 0:
        state = "会话cookie(浏览器关闭即失效)"
    else:
        exp_unix = int(exp_utc / 1e6) - WEBKIT_EPOCH_OFFSET
        if exp_unix < now:
            state = f"已过期({datetime.datetime.fromtimestamp(exp_unix)})"
        else:
            state = f"有效到 {datetime.datetime.fromtimestamp(exp_unix)}"
    print(f"  {name:30s} host={host:14s} {state} httponly={httponly}")
