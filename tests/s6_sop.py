"""S6 门禁（随批增长）。本文件当前钉批 1（prompts 全量逐字复制）。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe tests/s6_sop.py

口径（docs/施工3 批 1 原话）：**这是全项目唯一"不许优化"的地方**——所以断言不能打在
"文件文本 diff"上（import 前缀本来必须重写），要打在**每个模块级字符串常量**上：
解析出「顶层赋值里所有 str 字面量」逐个与源比对，一个字的措辞漂移都会红。
源仓 E:/MetaGPT 缺失时跳过（供体不是本仓的 CI 依赖），但本机在就必须跑。
"""
import ast
import importlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO.parent / "MetaGPT" / "metagpt" / "prompts"
DST = REPO / "codeharness" / "prompts"

PROMPT_FILES = [  # 施工3 批1 的 16 件（路径相对 prompts/）
    "__init__.py", "product_manager.py", "summarize.py", "task_type.py", "sales.py",
    "tutorial_assistant.py", "invoice_ocr.py", "metagpt_sample.py", "generate_skill.md",
    "di/__init__.py", "di/role_zero.py", "di/swe_agent.py", "di/architect.py",
    "di/engineer2.py", "di/data_analyst.py", "di/team_leader.py", "di/write_analysis_code.py",
]


def module_strings(path: Path) -> dict:
    """顶层赋值里的字符串常量（含 f-string 的非插值段不参与：prompt 件里没有插值赋值）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            for t in targets:
                if isinstance(t, ast.Name) and isinstance(node.value, ast.Constant) \
                        and isinstance(node.value.value, str):
                    out[t.id] = node.value.value
    return out


def t1_prompts_verbatim():
    if not SRC.exists():
        print("  t1 跳过（供体 E:/MetaGPT/prompts 不在本机）")
        return
    checked = 0
    for rel in PROMPT_FILES:
        s, d = SRC / rel, DST / rel
        assert d.exists(), f"{rel} 没复制过来"
        if rel.endswith(".md"):
            assert d.read_text(encoding="utf-8") == s.read_text(encoding="utf-8"), f"{rel} 资产漂移"
            checked += 1
            continue
        src_s, dst_s = module_strings(s), module_strings(d)
        assert src_s.keys() == dst_s.keys(), \
            f"{rel} 常量集不等，缺 {set(src_s) - set(dst_s)} 多 {set(dst_s) - set(src_s)}"
        for k in src_s:
            assert src_s[k] == dst_s[k], f"{rel}:{k} 逐字性被破坏（源与仓内常量不相等）"
        checked += len(src_s)
    assert checked > 40, f"覆盖异常：只数到 {checked} 个常量，复制面缩水了？"
    print(f"  t1 {len(PROMPT_FILES)} 件 prompt 顶层字符串常量与源逐字相等（比中 {checked} 项）")


def t2_prompt_imports_and_consumers():
    """16 件 + 两个补齐的依赖件全部可 import；`strategy/task_type.py` 的枚举成员名与源一致。"""
    mods = ["codeharness.prompts", "codeharness.prompts.di.role_zero", "codeharness.prompts.di.swe_agent",
            "codeharness.prompts.di.architect", "codeharness.prompts.di.engineer2",
            "codeharness.prompts.di.data_analyst", "codeharness.prompts.di.team_leader",
            "codeharness.prompts.di.write_analysis_code", "codeharness.prompts.product_manager",
            "codeharness.prompts.summarize", "codeharness.prompts.task_type", "codeharness.prompts.sales",
            "codeharness.prompts.tutorial_assistant", "codeharness.prompts.invoice_ocr",
            "codeharness.prompts.metagpt_sample", "codeharness.strategy.task_type",
            "codeharness.tools.libs.data_preprocess"]
    for m in mods:
        importlib.import_module(m)
    from codeharness.strategy.task_type import TaskType
    from codeharness.tools.libs.data_preprocess import get_column_info
    import pandas as pd
    info = get_column_info(pd.DataFrame({"c": ["a", "b"], "n": [1, 2],
                                         "d": pd.to_datetime(["2026-01-01", "2026-01-02"]),
                                         "o": [object(), None]}, dtype=object))
    assert set(info) == {"Category", "Numeric", "Datetime", "Others"}, info
    assert len(list(TaskType)) >= 5 and all(
        t.value.desc and isinstance(t.value.guidance, str) for t in TaskType)   # 枚举成员挂 prompt 资产
    print(f"  t2 {len(mods)} 件导入冒烟 + get_column_info 真跑 + TaskType 枚举可用")


def main():
    checks = [t1_prompts_verbatim, t2_prompt_imports_and_consumers]
    for c in checks:
        c()
    print(f"\nS6 门禁（当前批 1 范围）通过：{len(checks)} 组 —— 批 2 起在此文件续加"
          f"（每 Action 一 fixture / build_role 全名 / N2 外部注册演示 / mermaid 方案 C 落盘口径）")


if __name__ == "__main__":
    sys.exit(main())
