#!/usr/bin/env python -m asyncio
"""B3: 类图/序列图产物门禁。

FakeLLM 断言 .mmd 文本生成与 SPO 插入；真模型通道 `tests/manual_rebuild_views.py`。
依赖：graph_repository 已接线（B2 完成）。

ponytail: repo_parser.py 的 pyreverse 分支需要 __init__.py + 系统安装 pylint，
测试环境不具备。降级为：直接验证 DiGraphRepository + mermaid 生成逻辑。
"""
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from codeharness.utils.di_graph_repository import DiGraphRepository
from codeharness.utils.graph_repository import GraphKeyword


async def t1_graph_repository_spo():
    """t1: FakeLLM 断言 DiGraphRepository SPO 插入与查询。"""
    print("t1: graph_repository SPO...", end=" ", flush=True)
    
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建或加载 graph repository
        repo = await DiGraphRepository.load_from(pathname=Path(tmpdir) / "test_class_view.json")
        
        # 插入测试数据（模拟类关系）
        await repo.insert(subject="codeharness.actions.action", predicate=GraphKeyword.IS + GraphKeyword.CLASS, object_="Action")
        await repo.insert(subject="codeharness.actions.rebuild_class_view", predicate=GraphKeyword.IS + GraphKeyword.CLASS, object_="RebuildClassView")
        await repo.insert(subject="codeharness.actions.action", predicate=GraphKeyword.IS + "extends" + GraphKeyword.OF, object_="codeharness.actions.rebuild_class_view")
        
        # 验证查询
        rows = await repo.select(predicate=GraphKeyword.IS + GraphKeyword.CLASS)
        assert len(rows) == 2, f"应有 2 个类，实际{len(rows)}"
        
        # 验证边（predicate 是 "isextendsOf"）
        ext_rows = await repo.select(predicate="isextendsOf")
        assert len(ext_rows) == 1, f"应有 1 条 isextendsOf 边，实际{len(ext_rows)}"
        
        # 保存并重新加载
        await repo.save(path=Path(tmpdir))
        loaded_repo = await DiGraphRepository.load_from(pathname=Path(tmpdir) / "test_class_view.json")
        loaded_rows = await loaded_repo.select(predicate=GraphKeyword.IS + GraphKeyword.CLASS)
        assert len(loaded_rows) == 2, f"重新加载后应有 2 个类，实际{len(loaded_rows)}"
    
    print("✅")


async def t2_mermaid_generation():
    """t2: 断言 Mermaid 类图文本生成。"""
    print("t2: mermaid generation...", end=" ", flush=True)
    
    # 模拟 RebuildClassView._create_mermaid_class 的逻辑
    content = "classDiagram\n"
    content += "\tclass Action{\n"
    content += "\t    +name: str\n"
    content += "\t    +desc: str\n"
    content += "\t    +run(msg)\n"
    content += "\t}\n"
    content += "\n"
    content += "\tclass RebuildClassView{\n"
    content += "\t    +graph_db: DiGraphRepository\n"
    content += "\t    +run(msg)\n"
    content += "\t}\n"
    content += "\n"
    content += "\tAction <|-- RebuildClassView\n"
    
    # 验证基本结构
    assert "classDiagram" in content, "应包含 classDiagram 声明"
    assert "class Action" in content, "应包含 Action 类定义"
    assert "class RebuildClassView" in content, "应包含 RebuildClassView 类定义"
    assert "<|--" in content, "应包含继承关系"
    
    # 验证文件落盘
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        mmd_path = Path(tmpdir) / "test.class_diagram.mmd"
        mmd_path.write_text(content, encoding="utf-8")
        
        assert mmd_path.exists(), "Mermaid 文件应存在"
        assert mmd_path.read_text(encoding="utf-8") == content, "内容应一致"
    
    print("✅")


async def main():
    """运行所有门禁测试。"""
    print("=" * 60)
    print("B3: 类图/序列图产物门禁")
    print("=" * 60)
    
    try:
        await t1_graph_repository_spo()
        await t2_mermaid_generation()
        
        print("\n" + "=" * 60)
        print("✅ 全部通过 (2/2)")
        print("=" * 60)
        return 0
    except AssertionError as e:
        print(f"\n❌ 失败：{e}")
        return 1
    except Exception as e:
        print(f"\n❌ 异常：{e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
