#!/usr/bin/env python -m asyncio
"""B6/批次0: 网关 token 压缩门禁（token 口径）。

真模型通道 `tests/manual_long_context.py`。断言全部打在「出口 messages 的实际 token 数」上，
不再用「条数 × threshold」旧口径（那是实现从按条数改按 token 前留下的，改完后 t2/t3 一直红）。
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from codeharness.configs.llm_config import LLMConfig
from codeharness.provider.gateway import LLMGateway
from codeharness.provider.fake import FakeLLM


def _mk_gateway(cfg):
    fake_llm = FakeLLM(responses=["test"])
    captured = []

    async def mock_ainvoke(msgs, **kw):
        captured.append(msgs)
        return AIMessage(content="test response")

    fake_llm.ainvoke = mock_ainvoke
    with patch.object(LLMGateway, "_build", return_value=fake_llm):
        gw = LLMGateway(cfg=cfg, cost_manager=None)
    return gw, captured


def _texts(msgs):
    return [m.content if hasattr(m, "content") else str(m) for m in msgs]


async def t1_compress_disabled():
    """t1: threshold=1.0 不触发压缩（保持原样）。"""
    print("t1: compress disabled...", end=" ", flush=True)
    gw, cap = _mk_gateway(LLMConfig(model="fake", context_length=100, compress_threshold=1.0))
    await gw.ainvoke([f"message {i}" for i in range(20)], tag="t1")
    assert len(cap[0]) == 20, f"不压缩应保留全部 20 条，实际{len(cap[0])}"
    print("✅")


async def t2_token_budget():
    """t2: 出口 messages 的 token 数 ≤ context_length × threshold。"""
    print("t2: token budget...", end=" ", flush=True)
    cfg = LLMConfig(model="fake", context_length=200, compress_threshold=0.5)  # 预算 100
    gw, cap = _mk_gateway(cfg)
    long_msgs = [f"message number {i} with some repeated filler text filler filler" for i in range(30)]
    await gw.ainvoke(long_msgs, tag="t2")
    out_tokens = gw._count_tokens_direct(cap[0])
    assert out_tokens <= 100, f"出口应 ≤100 tokens（200×0.5），实际{out_tokens}"
    assert len(cap[0]) < 30, f"应确有裁剪，实际保留{len(cap[0])}条"
    # 保留的是最近的：末条必须是最后一条原文
    assert _texts(cap[0])[-1] == long_msgs[-1], "post_cut 应保留最近一条"
    print("✅")


async def t3_minimum():
    """t3: 预算极小也至少保留 1 条非 system 消息。"""
    print("t3: compress minimum...", end=" ", flush=True)
    cfg = LLMConfig(model="fake", context_length=4, compress_threshold=0.1)  # 预算 0
    gw, cap = _mk_gateway(cfg)
    await gw.ainvoke([f"message {i}" for i in range(10)], tag="t3")
    assert len(cap[0]) >= 1, f"至少保留 1 条，实际{len(cap[0])}"
    print("✅")


async def t4_token_count_positive():
    """t4: _count_tokens_direct 给出正数 token 计数。"""
    print("t4: token count...", end=" ", flush=True)
    gw, _ = _mk_gateway(LLMConfig(model="fake", context_length=1000, compress_threshold=1.0))
    n = gw._count_tokens_direct([HumanMessage(content="hello world this is a test of token counting")])
    assert n > 5, f"该句 tiktoken 应 >5 tokens，实际{n}"
    print("✅")


async def t5_system_preserved():
    """t5: 压缩后 system 消息恒留（照源 base_llm.py）。"""
    print("t5: system preserved...", end=" ", flush=True)
    cfg = LLMConfig(model="fake", context_length=100, compress_threshold=0.5)  # 预算 50
    gw, cap = _mk_gateway(cfg)
    msgs = [SystemMessage(content="SYSTEM PROMPT that must survive compression")]
    msgs += [HumanMessage(content=f"filler message number {i} some more text here") for i in range(15)]
    await gw.ainvoke(msgs, tag="t5")
    types = [getattr(m, "type", getattr(m, "role", "")) for m in cap[0]]
    assert "system" in types, "system 必须幸存"
    assert types[0] == "system", f"system 应在队首，实际队首={types[0] if types else '空'}"
    print("✅")


async def main():
    print("=" * 60)
    print("批次0: 网关 token 压缩门禁（token 口径）")
    print("=" * 60)
    try:
        await t1_compress_disabled()
        await t2_token_budget()
        await t3_minimum()
        await t4_token_count_positive()
        await t5_system_preserved()
        print("\n" + "=" * 60)
        print("✅ 全部通过 (5/5)")
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
    sys.exit(asyncio.run(main()))
