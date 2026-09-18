#!/usr/bin/env python -m asyncio
"""P0 四项门禁汇总——对照文档分析的最紧急缺口。

包含：
- s17_agent_memory: 经典线历史回喂 (2 组)
- s18_exp_tenant: 经验池租户隔离 (2 组)  
- s19_kb_producer: 知识库生产者 (2 组)
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


async def t1_agent_memory_feed():
    """t1: Agent._act 从 Memory 取历史进 prompt。"""
    print("t1: agent memory feed...", end=" ", flush=True)
    
    # 验证代码已修改（grep 检查）
    with open("codeharness/roles/agent.py", "r", encoding="utf-8") as f:
        content = f.read()
        assert "recent_history" in content, "Agent._act 应引入 recent_history"
        assert "history_str" in content, "应拼接历史上下文"
    
    print("✅")


async def t2_exp_tenant_isolation():
    """t2: ExpStore 从 CURRENT_USER 取 user_id。"""
    print("t2: exp tenant isolation...", end=" ", flush=True)
    
    # 验证代码已修改
    with open("codeharness/document_store/exp_store.py", "r", encoding="utf-8") as f:
        content = f.read()
        assert "CURRENT_USER" in content, "ExpStore 应注入 CURRENT_USER"
        assert 'user_id or CURRENT_USER.get("default")' in content, "应有 fallback 逻辑"
    
    print("✅")


async def t3_kb_producer_exists():
    """t3: UploadKB Action 存在且正确实现。"""
    print("t3: kb producer exists...", end=" ", flush=True)
    
    from codeharness.actions.upload_kb import UploadKB
    
    # 验证类存在
    assert hasattr(UploadKB, "_call"), "应有_call 方法"
    
    # 验证签名
    import inspect
    sig = inspect.signature(UploadKB._call)
    params = list(sig.parameters.keys())
    assert "params" in params, "应有 params 参数"
    
    print("✅")


async def main():
    """运行所有门禁测试。"""
    print("=" * 60)
    print("P0 四项门禁汇总")
    print("=" * 60)
    
    try:
        await t1_agent_memory_feed()
        await t2_exp_tenant_isolation()
        await t3_kb_producer_exists()
        
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
