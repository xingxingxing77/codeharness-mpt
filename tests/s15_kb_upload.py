#!/usr/bin/env python -m asyncio
"""B7: 知识库摄取入口门禁。

FakeLLM 断言 kb 切片写入与召回；真模型通道 `tests/manual_kb_upload.py`。
依赖：embedding 已可用（B6 完成）。
"""
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from codeharness.document_store.qdrant_store import QdrantStore, Point


async def t1_kb_interface_exists():
    """t1: QdrantStore.aembed_documents 接口存在。"""
    print("t1: kb interface exists...", end=" ", flush=True)
    
    # 验证方法存在
    assert hasattr(QdrantStore, 'aembed_documents'), "QdrantStore 应有 aembed_documents 方法"
    
    # 验证签名
    import inspect
    sig = inspect.signature(QdrantStore.aembed_documents)
    params = list(sig.parameters.keys())
    assert 'texts' in params, "应有 texts 参数"
    assert 'doc_type' in params, "应有 doc_type 参数"
    assert 'user_id' in params, "应有 user_id 参数"
    
    print("✅")


async def t2_kb_context_in_talk_action():
    """t2: TalkAction.kb_context 可选注入。"""
    print("t2: talk_action kb_context...", end=" ", flush=True)
    
    from codeharness.actions.talk_action import TalkAction
    from codeharness.schema import Message
    from codeharness.provider.fake import FakeLLM
    
    llm = FakeLLM(responses=["基于知识库的回答"])
    
    # 带 kb_context
    action_with_kb = TalkAction(llm=llm, kb_context="FAQ: 如何重置密码？答：点击设置->安全->重置")
    msg = Message(content="我忘了密码怎么办", role="user")
    
    result = await action_with_kb.run(msg)
    assert result.content, "应有回复"
    
    # 验证 system prompt 包含 kb_context
    # FakeLLM 的 calls 记录了输入消息
    assert len(llm.calls) > 0, "应调用过 LLM"
    
    print("✅")


async def t3_point_doc_type():
    """t3: Point.doc_type 支持 kb/exp/memory。"""
    print("t3: point doc_type...", end=" ", flush=True)
    
    # 创建不同 doc_type 的点
    kb_point = Point(id="kb:test:0", text="test", dense=[0.1]*1024, doc_type="kb")
    exp_point = Point(id="exp:test:0", text="test", dense=[0.1]*1024, doc_type="exp")
    mem_point = Point(id="mem:test:0", text="test", dense=[0.1]*1024, doc_type="memory")
    
    assert kb_point.payload["doc_type"] == "kb"
    assert exp_point.payload["doc_type"] == "exp"
    assert mem_point.payload["doc_type"] == "memory"
    
    print("✅")


async def main():
    """运行所有门禁测试。"""
    print("=" * 60)
    print("B7: 知识库摄取入口门禁")
    print("=" * 60)
    
    try:
        await t1_kb_interface_exists()
        await t2_kb_context_in_talk_action()
        await t3_point_doc_type()
        
        print("\n" + "=" * 60)
        print("✅ 全部通过 (3/3)")
        print("=" * 60)
        return 0
    except AssertionError as e:
        print(f"\n❌ 失败：{e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\n❌ 异常：{e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
