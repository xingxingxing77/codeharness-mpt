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


def _msg_setup():
    from langchain_core.messages import HumanMessage
    from codeharness.configs.compress_msg_config import CompressType
    cfg = LLMConfig(model="fake", context_length=100, compress_threshold=1.0)  # 不经 ainvoke 门，直测裁剪
    gw, _ = _mk_gateway(cfg)
    h0 = HumanMessage(content="HEAD " + "filler " * 40)   # 长，塞不下
    h1 = HumanMessage(content="one")
    h2 = HumanMessage(content="two")
    return gw, [h0, h1, h2], CompressType, 6              # keep=6：h1+h2 整条够，h0 不够


def t6_post_by_msg_and_token():
    """POST_*：保末尾消息完整；by_msg 丢弃边界首条，by_token 把边界截断填余额（token 更多）。"""
    print("t6: post by_msg vs by_token...", end=" ", flush=True)
    gw, msgs, CT, keep = _msg_setup()
    by_msg = gw._compress_messages(list(msgs), keep, CT.POST_CUT_BY_MSG)
    by_tok = gw._compress_messages(list(msgs), keep, CT.POST_CUT_BY_TOKEN)
    assert by_msg[-1].content == "two", "POST 应保住末尾消息"
    # by_msg 整条丢弃塞不下的边界条；by_token 把它截断填余额 → 多一条、总 token 更大
    assert len(by_msg) == 2, f"by_msg 应只留 h1,h2，实际{len(by_msg)}"
    assert len(by_tok) == 3, f"by_token 应额外留截断后的边界条，实际{len(by_tok)}"
    assert gw._count_tokens_direct(by_tok) > gw._count_tokens_direct(by_msg), "by_token 应填更多余额"
    assert by_tok[-1].content == "two" and by_tok[-2].content == "one", "by_token 也应保末尾完整"
    print("✅")


def t7_pre_by_msg_and_token():
    """PRE_*：从头往后塞，保首条完整；边界出现在末尾（by_msg 丢、by_token 截头填）。"""
    print("t7: pre by_msg vs by_token...", end=" ", flush=True)
    from langchain_core.messages import HumanMessage
    from codeharness.configs.compress_msg_config import CompressType as CT
    cfg = LLMConfig(model="fake", context_length=100, compress_threshold=1.0)
    gw, _ = _mk_gateway(cfg)
    msgs = [HumanMessage(content="one"), HumanMessage(content="two"),
            HumanMessage(content="tail " + "filler " * 40)]
    by_msg = gw._compress_messages(list(msgs), 6, CT.PRE_CUT_BY_MSG)
    by_tok = gw._compress_messages(list(msgs), 6, CT.PRE_CUT_BY_TOKEN)
    assert by_msg[0].content == "one", "PRE 应保住首条"
    assert len(by_msg) == 2, f"PRE by_msg 应留 one,two 丢边界 tail，实际{len(by_msg)}"
    assert len(by_tok) == 3 and by_tok[0].content == "one" and by_tok[1].content == "two", \
        "PRE by_token 应保 one,two + 截断 tail"
    assert by_tok[2].content != msgs[2].content, "tail 应被截断而非整条保留"
    print("✅")


def t8_no_compress_passthrough():
    """NO_COMPRESS 原样返回。"""
    print("t8: no compress...", end=" ", flush=True)
    gw, msgs, CT, keep = _msg_setup()
    assert gw._compress_messages(list(msgs), keep, CT.NO_COMPRESS) == msgs
    print("✅")


def t9_config_helpers():
    """CompressType.get_type/cut_types（批次5 补源残缺）。"""
    print("t9: config helpers...", end=" ", flush=True)
    from codeharness.configs.compress_msg_config import CompressType as CT
    assert CT.get_type("bogus") == CT.NO_COMPRESS and CT.get_type("") == CT.NO_COMPRESS
    assert CT.get_type("post_cut_by_msg") == CT.POST_CUT_BY_MSG
    assert len(CT.cut_types()) == 4, f"四个真裁剪策略，实际{CT.cut_types()}"
    print("✅")


async def main():
    print("=" * 60)
    print("批次0/5: 网关 token 压缩门禁（token 口径 + 四策略）")
    print("=" * 60)
    try:
        await t1_compress_disabled()
        await t2_token_budget()
        await t3_minimum()
        await t4_token_count_positive()
        await t5_system_preserved()
        t6_post_by_msg_and_token()
        t7_pre_by_msg_and_token()
        t8_no_compress_passthrough()
        t9_config_helpers()
        print("\n" + "=" * 60)
        print("✅ 全部通过 (9/9)")
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
