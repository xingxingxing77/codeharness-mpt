"""docx 读取（源 metagpt/utils/read_docx.py 的最小等价）：python-docx 未装时给出明确提示。"""
from pathlib import Path


def read_docx(filename: str) -> str:
    try:
        import docx
    except ImportError:
        raise ImportError("读取 docx 需要 python-docx：pip install python-docx")
    doc = docx.Document(filename)
    return "\n".join(p.text for p in doc.paragraphs)
