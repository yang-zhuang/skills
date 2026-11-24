"""读取本 prompts/ 目录下的提示词模板全文（utf-8）。"""

from pathlib import Path


def load_prompt(name):
    """读 prompts/ 目录下的模板全文（utf-8）。"""
    return (Path(__file__).resolve().parent / name).read_text(encoding="utf-8")
