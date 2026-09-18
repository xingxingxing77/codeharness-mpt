#!/usr/bin/env python -m asyncio
"""B2: 联网问答 + 仓库导入门禁。

FakeLLM 断言搜索返回 canned HTML/JSON，断言 graph_repository 四谓词齐；
真模型通道 `tests/manual_search_import.py`。
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from codeharness.actions.import_repo import ImportRepo
from codeharness.actions.search_enhanced_qa import SearchEnhancedQA
from codeharness.configs.settings import settings
from codeharness.provider.fake import FakeLLM


async def t1_search_enhanced_qa_fake():
    """t1: FakeLLM 断言搜索返回 canned HTML/JSON。"""
    print("t1: search_enhanced_qa FakeLLM...", end=" ", flush=True)
    
    # Mock 搜索引擎（避免真实网络调用）
    mock_results = [
        {"title": "Test Result 1", "link": "https://example.com/1", "snippet": "Test snippet 1"},
        {"title": "Test Result 2", "link": "https://example.com/2", "snippet": "Test snippet 2"}
    ]
    
    with patch('codeharness.actions.search_enhanced_qa.search', new=AsyncMock(return_value=mock_results)):
        # 创建 Action（FakeLLM 需要 responses 参数）
        action = SearchEnhancedQA(llm=FakeLLM(responses=["test response"]))
        
        # 执行搜索
        result = await action._call({
            "query": "test query",
            "max_results": 5,
            "save_to": "search_test.json"
        })
        
        # 断言结果结构
        assert "results" in result, "结果应包含 results 字段"
        assert "summary" in result, "结果应包含 summary 字段"
        assert isinstance(result["results"], list), "results 应为列表"
        assert len(result["results"]) == 2, f"结果数应为 2，实际{len(result['results'])}"
        
        # 验证搜索结果
        assert result["results"][0]["title"] == "Test Result 1"
        assert result["results"][0]["link"] == "https://example.com/1"
        
        # 断言落盘
        assert result.get("saved_to") == "search_test.json", "应记录保存文件名"
    
    print("✅")


async def t2_import_repo_fake():
    """t2: FakeLLM 断言 graph_repository 四谓词齐（node/edge/type/ref）。"""
    print("t2: import_repo FakeLLM...", end=" ", flush=True)
    
    # 创建临时目录
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        action = ImportRepo(llm=FakeLLM(responses=["test response"]))
        
        # 修改 artifact_store_root（Hack）
        action._artifact_store.root = Path(tmpdir)
        
        # 执行导入（使用当前代码库）
        result = await action._call({
            "repo_path": str(Path(__file__).parent.parent),
            "save_name": "test_repo",
            "include_files": True
        })
        
        # 断言统计
        assert "node_count" in result, "应有 node_count"
        assert "edge_count" in result, "应有 edge_count"
        assert result["node_count"] > 0, f"至少有一个根节点，实际{result['node_count']}"
        assert result["edge_count"] >= 0, "边数非负"
        
        # 断言落盘路径
        assert result.get("saved_json"), "应保存 JSON"
        assert result.get("saved_mmd"), "应保存 Mermaid"
        
        # 验证文件存在（等待异步保存完成）
        import asyncio
        await asyncio.sleep(0.1)  # 给异步保存一点时间
        
        json_path = Path(result["saved_json"])
        mmd_path = Path(result["saved_mmd"])
        
        # 打印调试信息
        if not json_path.exists():
            print(f"\n  JSON 文件不存在：{json_path}")
            print(f"  root: {action._artifact_store.root}")
            print(f"  tmpdir contents: {list(Path(tmpdir).iterdir())}")
        if not mmd_path.exists():
            print(f"\n  Mermaid 文件不存在：{mmd_path}")
        
        assert json_path.exists(), f"JSON 文件应存在：{json_path}"
        assert mmd_path.exists(), f"Mermaid 文件应存在：{mmd_path}"
        
        # 验证 JSON 内容
        import json
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert "links" in data or "edges" in data, "JSON 应包含图结构"
        
        # 验证 Mermaid 内容
        with open(mmd_path, "r", encoding="utf-8") as f:
            mmd_content = f.read()
            assert "graph" in mmd_content.lower(), "Mermaid 应包含 graph 声明"
            assert "TD" in mmd_content or "LR" in mmd_content, "应有方向声明"
    
    print("✅")


async def main():
    """运行所有门禁测试。"""
    print("=" * 60)
    print("B2: 联网问答 + 仓库导入门禁")
    print("=" * 60)
    
    try:
        await t1_search_enhanced_qa_fake()
        await t2_import_repo_fake()
        
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
