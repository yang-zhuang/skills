"""批量 OCR PDF（PaddleOCR-VL）— skill 自包含版。

来源
    改编自兄弟仓库 pdf-ocr-toolkit/ocr_pdfs.py（主版本在那边）。本版为分发做的改动：
    - 配置走 _config.py（系统环境变量 > 项目根 .env > 默认值），键名加 OCR_ 前缀防撞
    - 去掉 python-dotenv / tqdm 依赖（进度用普通打印）
    - 页数缓存写到 outputs/ 下，不污染 skill 目录
    解析逻辑与主版本一致，改动两边要同步。

功能
    - 递归扫描 --input 目录下所有 *.pdf
    - 每个 PDF 在「它所在文件夹的上一级」新建 OUT_DIR_NAME 目录，里面放 imgs/ + paddle_ocr_vl.json
    - 已生成 JSON 的 PDF 直接跳过（断点续传）；失败的只打印不写 JSON，下次重跑会重试
    - 两种模式：official（aistudio API，要 TOKEN）| local（私有化部署地址）

配置键（项目根 .env 或系统环境变量，模板见 .env.example）
    OCR_INPUT_DIR     输入目录（也可用 --input 覆盖）
    OCR_TOKEN         official 模式必填（PaddleOCR aistudio 的 token）
    OCR_JOB_URL       默认 https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
    OCR_MODEL         默认 PaddleOCR-VL-1.6
    OCR_MODE          official | local，默认 official
    OCR_LOCAL_API_URL local 模式地址，默认 http://127.0.0.1:8080/layout-parsing
    OCR_LOCAL_API_TOKEN local 模式可选 token
    OCR_OUT_DIR_NAME  默认 paddle_ocr_vl_1_6（落盘目录名）
    OCR_JSON_NAME     默认 paddle_ocr_vl.json
    OCR_ORDER         path | pages（pages=页数少的先解析），默认 path
    OCR_POLL_INTERVAL / OCR_POLL_TIMEOUT   轮询间隔/超时秒数，默认 5 / 3600

用法
    python ocr_pdfs.py --input <目录>              # official 模式，读 .env 的 TOKEN
    python ocr_pdfs.py --input <目录> --mode local # 临时切私有化部署
    python ocr_pdfs.py --input <目录> --order pages --limit 2

OCR 产物之后接：python paddle2md.py <json或目录>   # paddle json -> markdown
"""

import argparse
import base64
import glob
import json
import os
import shutil
import sys
import time

import requests
from pypdf import PdfReader

import _config

_parser = argparse.ArgumentParser()
_parser.add_argument("--input", help="覆盖 .env 的 OCR_INPUT_DIR")
_parser.add_argument("--mode", choices=["official", "local"], help="覆盖 .env 的 OCR_MODE")
_parser.add_argument("--order", choices=["path", "pages"], help="覆盖 .env 的 OCR_ORDER")
_parser.add_argument("--dir-name", help="覆盖 .env 的 OCR_OUT_DIR_NAME")
_parser.add_argument("--limit", type=int, default=0, help="只处理前 N 个 PDF（测试用）")
args = _parser.parse_args()

INPUT_DIR = args.input or _config.get("OCR_INPUT_DIR", "")
MODE = (args.mode or _config.get("OCR_MODE", "official")).strip().lower()   # official | local
ORDER = (args.order or _config.get("OCR_ORDER", "path")).strip().lower()    # path | pages
OUT_DIR_NAME = args.dir_name or _config.get("OCR_OUT_DIR_NAME", "paddle_ocr_vl_1_6")
JSON_NAME = _config.get("OCR_JSON_NAME", "paddle_ocr_vl.json")
POLL_INTERVAL = int(_config.get("OCR_POLL_INTERVAL", "5"))
POLL_TIMEOUT = int(_config.get("OCR_POLL_TIMEOUT", "3600"))

OFFICIAL_JOB_URL = _config.get("OCR_JOB_URL", "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs")
OFFICIAL_TOKEN = _config.get("OCR_TOKEN", "")
OFFICIAL_MODEL = _config.get("OCR_MODEL", "PaddleOCR-VL-1.6")

LOCAL_API_URL = _config.get("OCR_LOCAL_API_URL", "http://127.0.0.1:8080/layout-parsing")
LOCAL_API_TOKEN = _config.get("OCR_LOCAL_API_TOKEN", "")

# 页数缓存：{PDF 绝对路径: [mtime_ns, 字节数, 页数]}，ORDER=pages 时避免每次重启都重数一遍
CACHE_PATH = os.path.join(_config.PROJECT_ROOT, "outputs", "ocr-page-cache.json")


def log(msg):
    print(msg)


def http_retry(fn, tries=3, wait=5):
    """瞬时网络错误（连接被重置 / SSL 中断 / 响应截断）当场重试，重试耗尽才抛出"""
    for i in range(tries):
        try:
            return fn()
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.JSONDecodeError) as e:
            if i == tries - 1:
                raise
            log("    网络抖动，%ds 后重试（第 %d/%d 次）：%s" % (wait, i + 1, tries - 1, type(e).__name__))
            time.sleep(wait)


def win_long(p):
    """Windows 长路径（>240 字符）加 \\\\?\\ 前缀"""
    p = os.path.abspath(p)
    return "\\\\?\\" + p if len(p) > 240 else p


def count_pages(pdf_path):
    """本地读 PDF 页数（pypdf）；读不了返回 None"""
    try:
        return len(PdfReader(win_long(pdf_path)).pages)
    except Exception:
        return None


def valid_json(p):
    """文件存在且能解析（上次写到一半崩掉的会被判为无效）"""
    try:
        with open(win_long(p), encoding="utf-8") as f:
            json.load(f)
        return True
    except Exception:
        return False


def save_image(value, dest):
    """value 是图片 URL（官方 API）或 base64 字符串（私有化部署）"""
    os.makedirs(os.path.dirname(win_long(dest)), exist_ok=True)
    data = requests.get(value, timeout=120).content if value.startswith("http") else base64.b64decode(value)
    with open(win_long(dest), "wb") as f:
        f.write(data)


def ocr_official(pdf_path):
    """官方 API：提交任务 -> 轮询 -> 拉 jsonl，返回每页 layoutParsingResults"""
    headers = {"Authorization": "bearer %s" % OFFICIAL_TOKEN}

    def submit():
        f.seek(0)   # 重试要从头再传一遍
        return requests.post(
            OFFICIAL_JOB_URL,
            headers=headers,
            data={"model": OFFICIAL_MODEL, "optionalPayload": json.dumps(OPTIONAL_PAYLOAD)},
            files={"file": f},
        )

    with open(win_long(pdf_path), "rb") as f:
        resp = http_retry(submit)
    if resp.status_code != 200:
        raise RuntimeError("提交失败 %d: %s" % (resp.status_code, resp.text[:300]))
    job_id = resp.json()["data"]["jobId"]
    log("    jobId=%s" % job_id)

    deadline = time.time() + POLL_TIMEOUT
    while True:
        data = http_retry(lambda: requests.get(
            "%s/%s" % (OFFICIAL_JOB_URL, job_id), headers=headers, timeout=60).json())["data"]
        state = data["state"]
        if state == "done":
            jsonl = http_retry(lambda: requests.get(data["resultUrl"]["jsonUrl"], timeout=300).text)
            results = []
            for line in jsonl.strip().split("\n"):
                if line.strip():
                    results.extend(json.loads(line)["result"]["layoutParsingResults"])
            return results
        if state == "failed":
            raise RuntimeError("任务失败: %s" % data.get("errorMsg"))
        if time.time() > deadline:
            raise RuntimeError("轮询超时（%ds）" % POLL_TIMEOUT)
        prog = data.get("extractProgress") or {}
        log("    %s %s/%s" % (state, prog.get("extractedPages", "?"), prog.get("totalPages", "?")))
        time.sleep(POLL_INTERVAL)


def ocr_local(pdf_path):
    """私有化部署：POST base64 到 /layout-parsing，同步返回"""
    with open(win_long(pdf_path), "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    headers = {"Content-Type": "application/json"}
    if LOCAL_API_TOKEN:
        headers["Authorization"] = "token %s" % LOCAL_API_TOKEN
    resp = http_retry(lambda: requests.post(
        LOCAL_API_URL, json={"file": b64, "fileType": 0, **OPTIONAL_PAYLOAD},
        headers=headers, timeout=POLL_TIMEOUT))
    if resp.status_code != 200:
        raise RuntimeError("请求失败 %d: %s" % (resp.status_code, resp.text[:300]))
    return resp.json()["result"]["layoutParsingResults"]


OPTIONAL_PAYLOAD = {
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useChartRecognition": False,
}


def process(pdf_path):
    """OCR 一个 PDF，结果写进 上一级目录/OUT_DIR_NAME/"""
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(pdf_path))), OUT_DIR_NAME)
    json_path = os.path.join(out_dir, JSON_NAME)

    if os.path.exists(win_long(json_path)):
        if valid_json(json_path):
            log("  已存在，跳过：%s" % json_path)
            return "skip"
        log("  上次结果损坏，重新解析：%s" % json_path)
        os.remove(win_long(json_path))

    imgs_dir = os.path.join(out_dir, "imgs")
    shutil.rmtree(win_long(imgs_dir), ignore_errors=True)   # 清掉上次失败留下的半成品
    os.makedirs(win_long(imgs_dir), exist_ok=True)

    try:
        results = ocr_official(pdf_path) if MODE == "official" else ocr_local(pdf_path)
        if not results:
            raise RuntimeError("接口没有返回任何页面")

        pages = []
        for idx, res in enumerate(results):
            markdown = res["markdown"]
            items = []
            for rel_path, img in (markdown.get("images") or {}).items():
                rel_path = "imgs/" + os.path.basename(rel_path.replace("\\", "/"))
                save_image(img, os.path.join(out_dir, rel_path))
                items.append({"label": "fig", "figure_path": rel_path})
            items.append({"page_index": idx, "page_content": markdown["text"]})
            pages.append(items)

        with open(win_long(json_path), "w", encoding="utf-8") as f:
            json.dump({"total_pages": len(pages), "pages": pages}, f, ensure_ascii=False, indent=2)
    except Exception:
        shutil.rmtree(win_long(imgs_dir), ignore_errors=True)   # 失败不留半成品，下次完整重试
        if os.path.exists(win_long(json_path)):
            os.remove(win_long(json_path))                      # 连同写了一半的 JSON 一起清掉
        raise

    log("  完成 %d 页 -> %s" % (len(pages), out_dir))
    return "ok"


def main():
    if not INPUT_DIR:
        raise SystemExit("请在 .env 里设置 OCR_INPUT_DIR，或用 --input 指定")
    if MODE == "official" and not OFFICIAL_TOKEN:
        raise SystemExit("请在 .env 里设置 OCR_TOKEN（PaddleOCR aistudio 的 token）")

    pdfs = sorted(set(glob.glob(os.path.join(INPUT_DIR, "**", "*.pdf"), recursive=True))
                  | set(glob.glob(os.path.join(INPUT_DIR, "**", "*.PDF"), recursive=True)))
    if not pdfs:
        raise SystemExit("没找到 PDF：%s" % INPUT_DIR)

    counts = {}
    if ORDER == "pages":        # 先本地数页数，页数少的先解析（结果存缓存，下次直接复用）
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
        fresh = 0
        for pdf in pdfs:
            st = os.stat(win_long(pdf))
            hit = cache.get(pdf)
            if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:   # 文件没变过，直接用上次的页数
                counts[pdf] = hit[2]
            else:
                counts[pdf] = count_pages(pdf)
                cache[pdf] = [st.st_mtime_ns, st.st_size, counts[pdf]]
                fresh += 1
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        log("  页数缓存 %s：本次新数 %d 篇，复用 %d 篇" % (CACHE_PATH, fresh, len(pdfs) - fresh))
        pdfs.sort(key=lambda p: (counts[p] is None, counts[p] or 0))   # 页数读不出来的排最后

    if args.limit:
        pdfs = pdfs[:args.limit]
    print("扫描到 %d 个 PDF（%s），模式=%s，顺序=%s" % (len(pdfs), INPUT_DIR, MODE, ORDER))

    ok, skipped, failed = 0, 0, []
    start = time.time()
    for pdf in pdfs:
        pages_info = "（%s 页）" % counts[pdf] if counts.get(pdf) else ""
        log("  %s%s" % (pdf, pages_info))
        t0 = time.time()
        try:
            r = process(pdf)
            ok += r == "ok"
            skipped += r == "skip"
        except Exception as e:
            log("  失败，跳过下次重试：%s" % e)
            failed.append(pdf)
            continue
        log("  耗时 %.0fs" % (time.time() - t0))

    print("\n完成：成功 %d，跳过 %d，失败 %d，总耗时 %.1f 分钟"
          % (ok, skipped, len(failed), (time.time() - start) / 60))
    for pdf in failed:
        print("  失败：%s" % pdf)


if __name__ == "__main__":
    main()
