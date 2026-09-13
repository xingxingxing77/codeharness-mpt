"""代码解析：源 repo_parser.py 语义 → tree_sitter 按 class/function 级切分代码块，
供 KnowledgeBase.ingest（第 8 步）做代码库 RAG。tree_sitter 未装时回退正则粗切。"""
import re
from pathlib import Path
from pydantic import BaseModel, Field


class CodeChunk(BaseModel):
    """= 源 repo_parser.CodeBlock 等价物"""
    file: str = ""
    type: str = ""          # class / function / module
    name: str = ""
    content: str = ""


def _tree_sitter_chunks(code: str) -> list[tuple[str, str, str]]:
    """返回 [(type, name, code)]；仅 top-level 的 class/function_definition"""
    from tree_sitter import Language, Parser
    import tree_sitter_python
    parser = Parser(Language(tree_sitter_python.language()))
    tree = parser.parse(code.encode("utf-8"))
    out = []
    for node in tree.root_node.children:
        if node.type in ("function_definition", "class_definition"):
            name_node = node.child_by_field_name("name")
            name = code[name_node.start_byte:name_node.end_byte] if name_node else ""
            out.append((node.type.split("_")[0], name, code[node.start_byte:node.end_byte]))
    return out


def _regex_chunks(code: str) -> list[tuple[str, str, str]]:
    out = []
    pattern = re.compile(r"^(class |def |async def )(\w+)", re.MULTILINE)
    matches = list(pattern.finditer(code))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(code)
        kind = "class" if m.group(1).startswith("class") else "function"
        out.append((kind, m.group(2), code[m.start():end]))
    return out


def parse_file(file_path: str, root: str = "") -> list[CodeChunk]:
    """单文件切分：tree_sitter 优先，未安装则正则回退"""
    path = Path(file_path)
    if not path.exists() or path.suffix != ".py":
        return []
    code = path.read_text(encoding="utf-8", errors="replace")
    rel = str(path.relative_to(root)) if root and path.is_relative_to(root) else str(path)
    try:
        chunks = _tree_sitter_chunks(code)
    except ImportError:
        chunks = _regex_chunks(code)
    return [CodeChunk(file=rel, type=t, name=n, content=c) for t, n, c in chunks]


def parse_repository(repo_dir: str) -> list[CodeChunk]:
    """遍历目录下全部 .py 文件切分（源 parse_repository 语义）"""
    out = []
    for p in sorted(Path(repo_dir).rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        out.extend(parse_file(str(p), root=repo_dir))
    return out
