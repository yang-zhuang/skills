"""classify.py -- 文章类型判定（paper / blog / tutorial），写入任务包 meta.json。

用法:
    python classify.py <task_pack_dir>

读取任务包 input/source.md，调用 OpenAI 兼容 endpoint 判定类型，结果写回
meta.json 的 type 字段（paper | blog | tutorial | uncertain）。

环境变量（均有默认值）:
    ABRIDGE_LLM_BASE_URL   默认 http://127.0.0.1:8000/v1
    ABRIDGE_LLM_API_KEY    默认 EMPTY
    ABRIDGE_LLM_MODEL      默认 qwen（按实际 endpoint 的模型名改）

判定标准与 skills/content-abridge/references/retention-policy.md 各节"类型判定特征"一致。
置信不足时一律写 uncertain，不得猜。
"""
import json
import os
import sys
from pathlib import Path

import requests

PROMPT = """你判定一篇文章的类型，只输出一个词。三个候选:
- paper: 有摘要/关键词/编号章节/参考文献/Figure Table 编号/算法伪代码等学术特征
- blog: 第一人称观点表达、议题论述、可能引用他人言论，无学术格式
- tutorial: 步骤化操作指令、代码块命令行、版本号环境配置
三类特征都不明显或互相冲突时输出: uncertain

文章内容（截断）:
"""


def main():
    if len(sys.argv) != 2:
        sys.exit("用法: python classify.py <task_pack_dir>")
    pack = Path(sys.argv[1])
    source = pack / "input" / "source.md"
    meta_path = pack / "meta.json"
    if not source.is_file() or not meta_path.is_file():
        sys.exit(f"缺 {source} 或 {meta_path}（先跑 build_task_pack.py）")

    base_url = os.environ.get("ABRIDGE_LLM_BASE_URL", "http://127.0.0.1:8000/v1").rstrip("/")
    api_key = os.environ.get("ABRIDGE_LLM_API_KEY", "EMPTY")
    model = os.environ.get("ABRIDGE_LLM_MODEL", "qwen")

    text = source.read_text(encoding="utf-8")[:12000]
    try:
        resp = requests.post(f"{base_url}/chat/completions", timeout=120, json={
            "model": model,
            "messages": [{"role": "user", "content": PROMPT + text}],
            "temperature": 0,
            "max_tokens": 8,
        })
        resp.raise_for_status()
        answer = resp.json()["choices"][0]["message"]["content"].strip().lower()
    except Exception as e:
        sys.exit(f"LLM 调用失败（{base_url}）: {e}")

    vtype = answer if answer in ("paper", "blog", "tutorial", "uncertain") else "uncertain"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["type"] = vtype
    meta["type_raw_answer"] = answer
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"done: type = {vtype} (raw: {answer!r}) -> {meta_path}")


if __name__ == "__main__":
    main()
