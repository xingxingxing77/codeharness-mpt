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
    print("t1: agent content-no-pollution guard...", end=" ", flush=True)

    # 回归守卫：content 是下一动作的工作载荷（RunPythonCode 直接 run_python_code(content)），
    # 不得把「## 历史对话」散文前缀塞进去（曾致 DataAnalyst 沙箱 SyntaxError）。跨动作上下文走 instruct_content 透传。
    with open("codeharness/roles/agent.py", "r", encoding="utf-8") as f:
        content = f.read()
        assert "## 历史对话" not in content, "_act 不得往 msg.content 前缀历史对话（会污染代码型 Action）"
        assert "instruct_content=trig.instruct_content" in content, "跨动作上下文应经 instruct_content 透传"

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


async def t3_kb_producer_wired():
    """t3: 知识库生产者**有真调用点**（C3）。

    这格原先只断 `hasattr(UploadKB, "_call")` + 参数名叫 `params`——类的存在性与签名证明不了链路接通，
    正是 PLAN §2 C3 点名的病（端到端那道在 `tests/s15_kb_upload.py`）。现在钉三件真事实：
    注入口在位（门禁才不必挂真 embedding 服务）、收 .md（用户最常传的文档）、`server/` 里有调用者。"""
    print("t3: kb producer wired...", end=" ", flush=True)

    from codeharness.actions.upload_kb import SUPPORTED, UploadKB

    assert {"store", "embeddings"} <= set(UploadKB.model_fields),         "UploadKB 没有注入口 = 端到端门禁只能挂真 embedding 服务，那条链就测不到"
    assert ".md" in SUPPORTED, f"知识库不收 .md，用户最常传的文档进不去：{sorted(SUPPORTED)}"
    root = Path(__file__).resolve().parent.parent
    callers = [str(p.relative_to(root)) for p in (root / "server").rglob("*.py")
               if "UploadKB" in p.read_text(encoding="utf-8")]
    assert callers, "UploadKB 又回到零生产调用者（C3 判据原文要的就是这一格）"
    print(f"✅（调用者 {callers}）")


async def main():
    """运行所有门禁测试。"""
    print("=" * 60)
    print("P0 四项门禁汇总")
    print("=" * 60)
    
    try:
        await t1_agent_memory_feed()
        await t2_exp_tenant_isolation()
        await t3_kb_producer_wired()
        
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
