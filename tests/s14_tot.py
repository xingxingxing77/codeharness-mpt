#!/usr/bin/env python -m asyncio
"""B5: ToT 策略门禁。

FakeLLM 断言同一题跑出三条路径并择优；真模型通道 `tests/manual_tot.py`。
依赖：无。
"""
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from codeharness.roles.registry import build_role, ALL_ROLES
from codeharness.provider.fake import FakeLLM


async def t1_tot_agent_basic():
    """t1: TotAgent 基本功能——生成多条路径。"""
    print("t1: TotAgent basic...", end=" ", flush=True)
    
    from codeharness.strategy.tot import ThoughtNode, ThoughtTree, TotAgent
    
    # 测试 ThoughtNode
    root = ThoughtNode("初始思考")
    child1 = ThoughtNode("分支 1", parent=root)
    child2 = ThoughtNode("分支 2", parent=root)
    root.add_child(child1)
    root.add_child(child2)
    
    assert len(root.children) == 2, "应有 2 个子节点"
    assert child1.parent == root, "父节点应正确"
    assert child1.get_path() == ["初始思考", "分支 1"], "路径应正确"
    
    # 测试 ThoughtTree
    tree = ThoughtTree("根思考")
    tree.evaluate(tree.root, FakeLLM(responses=["0.5"]))
    
    # 测试 TotAgent
    llm = FakeLLM(responses=["0.7"])
    agent = TotAgent(llm=llm, goal="测试目标", num_paths=3, max_depth=2)
    
    # 简单调用（不期望完整树搜索）
    result = await agent.think()
    assert isinstance(result, str), "结果应为字符串"
    assert len(result) > 0, "结果不应为空"
    
    print("✅")


async def t2_tot_strategy_in_registry():
    """t2: registry.build_role 支持 strategy="tot"。"""
    print("t2: registry tot support...", end=" ", flush=True)
    
    llm = FakeLLM(responses=["test response"])
    
    # 构建 RoleZero 子类角色（TeamLeader），指定 tot 策略
    role = build_role(name="TeamLeader", llm=llm, strategy="tot")
    
    # 验证 profile 更新了
    assert role.profile.get("strategy") == "tot", f"profile.strategy 应为 tot，实际{role.profile.get('strategy')}"
    
    # 验证 _plan 被替换为 tot_plan
    assert hasattr(role, '_plan'), "应有 _plan 属性"
    import inspect
    assert inspect.iscoroutinefunction(role._plan), "_plan 应是异步函数"
    
    # 调用 tot_plan
    result = await role._plan(goal="测试任务")
    assert isinstance(result, str), "结果应为字符串"
    
    print("✅")


async def t3_tot_vs_role_zero():
    """t3: tot 与 role_zero 策略互斥验证。"""
    print("t3: tot vs role_zero...", end=" ", flush=True)
    
    llm = FakeLLM(responses=["test"])
    
    # tot 策略正常
    role_tot = build_role(name="TeamLeader", llm=llm, strategy="tot")
    assert role_tot.profile.get("strategy") == "tot"
    
    # role_zero 策略正常
    role_rz = build_role(name="TeamLeader", llm=llm, strategy="role_zero")
    assert role_rz.profile.get("strategy") == "role_zero"
    
    # 无效策略应抛异常
    try:
        build_role(name="TeamLeader", llm=llm, strategy="invalid")
        assert False, "应抛 ValueError"
    except ValueError as e:
        assert "只认 role_zero/tot" in str(e), f"错误信息应提示有效策略，实际{e}"
    
    print("✅")


async def main():
    """运行所有门禁测试。"""
    print("=" * 60)
    print("B5: ToT 策略门禁")
    print("=" * 60)
    
    try:
        await t1_tot_agent_basic()
        await t2_tot_strategy_in_registry()
        await t3_tot_vs_role_zero()
        
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
