"""Linter：editor 改完文件后的语法闸门（源 `tools/libs/linter.py` 移植，砍掉 tree-sitter 层）。

源 233 行里真正干活的只有 python 那条链：flake8(可选) → `compile()` → basic_lint。
本件按标准库优先落地，**不引入 `grep_ast` 与 `tree_sitter_languages`**：
- 两者本机都未安装，且 `tree_sitter_languages` 上游已停止维护；
- `basic_lint` 只在 tree-sitter 能给非 Python 文件报语法错时才有增量，而源自己的
  `languages` 表就把 js/css/sql 全指到了 `fake_lint`（不校验）——Python 路径完全不经它；
- `tree_context()` / `traverse_tree()` 在源文件内部零调用者（OpenDevin 遗留）。

天花板：非 Python 文件与「缩进之外的语义问题」这里不报。升级路径是用
`Linter.set_linter("python", "ruff check")` 挂上 dev extra 里的 ruff，或补回 tree-sitter，
不必改调用方（`editor.py:206` 只认 `LintResult.text` / `.lines`）。
"""
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from codeharness.logs import logger

# 源靠 grep_ast.filename_to_lang 判定；这里只需要区分「Python 要真检、其余按源语义不检」
PYTHON_SUFFIXES = {".py", ".pyi"}


@dataclass
class LintResult:
    text: str
    lines: list


class Linter:
    def __init__(self, encoding="utf-8", root=None):
        self.encoding = encoding
        self.root = root
        self.languages = dict(
            python=self.py_lint,
            sql=self.fake_lint,      # 以下三件源即判"不校验"，本处照抄该语义
            css=self.fake_lint,
            js=self.fake_lint,
            javascript=self.fake_lint,
        )
        self.all_lint_cmd = None

    def set_linter(self, lang, cmd):
        if lang:
            self.languages[lang] = cmd
        else:
            self.all_lint_cmd = cmd

    def get_rel_fname(self, fname):
        return os.path.relpath(fname, self.root) if self.root else fname

    def get_abs_fname(self, fname):
        if os.path.isabs(fname):
            return fname
        return os.path.abspath(self.get_rel_fname(fname)) if os.path.isfile(fname) else self.get_rel_fname(fname)

    def run_cmd(self, cmd, rel_fname, code):
        process = subprocess.Popen((cmd + " " + rel_fname).split(), cwd=self.root,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        stdout, _ = process.communicate()
        errors = stdout.decode().strip()
        if process.returncode == 0:
            return None
        return LintResult(text=errors, lines=[extract_error_line_from(errors)])

    def lint(self, fname, cmd=None) -> Optional[LintResult]:
        code = Path(fname).read_text(self.encoding)
        absolute_fname = self.get_abs_fname(fname)
        cmd = (cmd or "").strip() or self.all_lint_cmd or self.languages.get(_lang_of(fname))
        if callable(cmd):
            return cmd(fname, absolute_fname, code)
        if cmd:
            return self.run_cmd(cmd, absolute_fname, code)
        return None

    def flake_lint(self, rel_fname, code):
        """源语义：只挑致命几项。flake8 不在 PATH 时返回 None，交给 compile 兜底。"""
        try:
            return self.run_cmd("flake8 --select=F821,F822,F831,E112,E113,E999,E902 --isolated", rel_fname, code)
        except FileNotFoundError:
            return None

    def py_lint(self, fname, rel_fname, code):
        return self.flake_lint(rel_fname, code) or lint_python_compile(fname, code)

    def fake_lint(self, fname, rel_fname, code):
        return None


def _lang_of(fname) -> str:
    return "python" if Path(fname).suffix.lower() in PYTHON_SUFFIXES else Path(fname).suffix.lstrip(".").lower()


def lint_python_compile(fname, code):
    """`compile()` 就是这一层的真值来源：报得出错文本与首错行，不需要 traceback 花招。"""
    try:
        compile(code, fname, "exec")
        return None
    except SyntaxError as err:
        line = err.lineno or 1
        marker = " " * max((err.offset or len(err.text or "")) - 1, 0) + "^"
        text = f"{type(err).__name__}: {err.msg}\n  line {line}: {err.text}\n  {marker}"
        return LintResult(text=text, lines=[line])


def extract_error_line_from(lint_error):
    """外部 linter 输出按 `<file>:<line>:<col>: <code> <msg>` 取行号。

    ⚠ 源用 `split(":")[1]`，隐含"文件名不含冒号"这个 POSIX 假设——Windows 的 `C:\\...` 会让
    parts[1] 变成路径片段，实测每条 flake8 报错的行号都退化成 1。改匹配 `:LINE:COL:`，盘符冒号不误命中。
    """
    m = re.search(r":(\d+):\d+:", lint_error)
    if m:
        return int(m.group(1))
    logger.warning(f"linter 输出里取不到行号，按第 1 行处理: {lint_error[:120]}")
    return 1
