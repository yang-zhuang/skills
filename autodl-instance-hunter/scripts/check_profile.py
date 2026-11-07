import sqlite3, os

# 真实 Chrome 默认 profile：按当前 Windows 用户推导，不写死本机用户名
base = os.path.join(
    os.path.expanduser("~"), "AppData", "Local", "Google", "Chrome", "User Data", "Default"
)
candidates = [
    os.path.join(base, "Cookies"),
    os.path.join(base, "Network", "Cookies"),
]
found = False
for c in candidates:
    if not os.path.exists(c):
        print("MISSING:", c)
        continue
    print("DB:", c)
    try:
        con = sqlite3.connect(f"file:{c}?mode=ro", uri=True)
        cur = con.cursor()
        cur.execute(
            "SELECT host_key, COUNT(*) FROM cookies "
            "WHERE host_key LIKE '%autodl%' GROUP BY host_key"
        )
        rows = cur.fetchall()
        con.close()
        if rows:
            found = True
            for r in rows:
                print("  autodl cookie host:", r[0], "count=", r[1])
        else:
            print("  no autodl.com cookies in this DB")
    except Exception as e:  # noqa
        print("  read error:", repr(e))
print("HAS_AUTODL_SESSION_COOKIE:", found)
