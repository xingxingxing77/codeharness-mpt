#!/usr/bin/env python -m asyncio
"""B6: 网关 token 压缩门禁。

FakeLLM 断言截断行为；真模型通道 `tests/manual_long_context.py`。
依赖：无。
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from codeharness.configs.llm_config import LLMConfig
from codeharness.provider.gateway import LLMGateway
from codeharness.provider.fake import FakeLLM


async def t1_compress_disabled():
    """t1: compress_threshold=1.0 时不压缩。"""
    print("t1: compress disabled...", end=" ", flush=True)
    
    cfg = LLMConfig(
        api_key="test",
        model="fake",
        context_length=100,
        compress_threshold=1.0  # 不压缩
    )
    
    # Mock _build 以返回 FakeLLM
    fake_llm = FakeLLM(responses=["test"])
    
    with patch.object(LLMGateway, '_build', return_value=fake_llm):
        gateway = LLMGateway(cfg=cfg, cost_manager=None)
        
        # 构造长消息列表（20 条）
        long_msgs = [f"message {i}" for i in range(20)]
        
        # Mock ainvoke 以捕获输入
        captured_msgs = []
        
        async def mock_ainvoke(msgs, **kw):
            captured_msgs.append(msgs)
            from langchain_core.messages import AIMessage
            return AIMessage(content="test response")
        
        fake_llm.ainvoke = mock_ainvoke
        
        # 调用
        await gateway.ainvoke(long_msgs, tag="test")
        
        # 断言：应收到全部 20 条消息
        assert len(captured_msgs) == 1, "应只调用一次"
        assert len(captured_msgs[0]) == 20, f"应收到全部 20 条消息，实际{len(captured_msgs[0])}"
    
    print("✅")


async def t2_compress_enabled():
    """t2: compress_threshold=0.5 时保留最近 50% 的消息。"""
    print("t2: compress enabled...", end=" ", flush=True)
    
    cfg = LLMConfig(
        api_key="test",
        model="fake",
        context_length=10,  # 最大 10 条消息
        compress_threshold=0.5  # 保留 50%
    )
    
    fake_llm = FakeLLM(responses=["test"])
    
    with patch.object(LLMGateway, '_build', return_value=fake_llm):
        gateway = LLMGateway(cfg=cfg, cost_manager=None)
        
        # 构造长消息列表（20 条）
        long_msgs = [f"message {i}" for i in range(20)]
        
        # Mock ainvoke 以捕获输入
        captured_msgs = []
        
        async def mock_ainvoke(msgs, **kw):
            captured_msgs.append(msgs)
            from langchain_core.messages import AIMessage
            return AIMessage(content="test response")
        
        fake_llm.ainvoke = mock_ainvoke
        
        # 调用
        await gateway.ainvoke(long_msgs, tag="test")
        
        # 断言：应收到 10 条消息（20 × 0.5）
        assert len(captured_msgs) == 1, "应只调用一次"
        expected_count = max(1, int(20 * 0.5))
        assert len(captured_msgs[0]) == expected_count, f"应收到{expected_count}条消息，实际{len(captured_msgs[0])}"
        
        # 断言：应是最近的 10 条（message 10-19）
        received_texts = [m.content if hasattr(m, 'content') else str(m) for m in captured_msgs[0]]
        assert received_texts[0] == "message 10", f"应从 message 10 开始，实际{received_texts[0]}"
        assert received_texts[-1] == "message 19", f"应以 message 19 结束，实际{received_texts[-1]}"
    
    print("✅")


async def t3_compress_minimum():
    """t3: 即使阈值很低，至少保留 1 条消息。"""
    print("t3: compress minimum...", end=" ", flush=True)
    
    cfg = LLMConfig(
        api_key="test",
        model="fake",
        context_length=10,  # 最大 10 条消息
        compress_threshold=0.1  # 保留 10%
    )
    
    fake_llm = FakeLLM(responses=["test"])
    
    with patch.object(LLMGateway, '_build', return_value=fake_llm):
        gateway = LLMGateway(cfg=cfg, cost_manager=None)
        
        # 构造长消息列表（20 条）
        long_msgs = [f"message {i}" for i in range(20)]
        
        # Mock ainvoke 以捕获输入
        captured_msgs = []
        
        async def mock_ainvoke(msgs, **kw):
            captured_msgs.append(msgs)
            from langchain_core.messages import AIMessage
            return AIMessage(content="test response")
        
        fake_llm.ainvoke = mock_ainvoke
        
        # 调用
        await gateway.ainvoke(long_msgs, tag="test")
        
        # 断言：至少保留 1 条消息
        assert len(captured_msgs) == 1, "应只调用一次"
        assert len(captured_msgs[0]) >= 1, f"应至少保留 1 条消息，实际{len(captured_msgs[0])}"
    
    print("✅")


async def main():
    """运行所有门禁测试。"""
    print("=" * 60)
    print("B6: 网关 token 压缩门禁")
    print("=" * 60)
    
    try:
        await t1_compress_disabled()
        await t2_compress_enabled()
        await t3_compress_minimum()
        
        print("\n" + "=" * 60)
        print("✅ 全部通过 (3/3)")
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
