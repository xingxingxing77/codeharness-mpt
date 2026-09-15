"""S2 门禁：LLM 网关 + 修复管线 + 配置与成本。零联网、零 token 花费。

覆盖 docs/施工1 的 S2 门禁三条（payload 快照 / FakeLLM 记账非零 / 不重复计数），
另加源符号面对齐、repair 组合档、只读计量与预算不回潮、未支持厂商不静默退回。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 F:/anaconda/python.exe tests/s2_gateway.py
"""
import asyncio

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from codeharness.configs.llm_config import LLMConfig, LLMType
from codeharness.configs.settings import RedisConfig, Settings, settings
from codeharness.provider.cost import CostManager, TokenCostManager
from codeharness.provider.fake import FakeLLM
from codeharness.provider.gateway import LLMGateway


def _fail(m):
    raise AssertionError(m)


def _gw(cfg=None, reply="好的", usage=None, structured_result=None, structured_error=None):
    """造一个不触网的 gateway：模型换成桩，成本账本独立。"""
    g = LLMGateway.__new__(LLMGateway)
    g.cfg = cfg or LLMConfig(model="gpt-4o", api_key="sk-test", max_token=1024)
    g.cost_manager = CostManager()
    g._model = _Stub(reply, usage, structured_result, structured_error)
    return g


class _Stub:
    def __init__(self, reply, usage, structured_result=None, structured_error=None):
        self.reply, self.usage = reply, usage or {"prompt_tokens": 100, "completion_tokens": 20}
        self.sr, self.se = structured_result, structured_error
        self.calls = []

    async def ainvoke(self, msgs, **kw):
        self.calls.append(("ainvoke", msgs, kw))
        if self.se:
            raise self.se
        return AIMessage(content=self.reply, response_metadata={"token_usage": self.usage})

    async def astream(self, msgs, **kw):
        self.calls.append(("astream", msgs, kw))
        for ch in self.reply:
            yield AIMessage(content=ch, response_metadata={"token_usage": self.usage})

    def bind(self, **kw):
        self.bound = kw
        return self

    def with_structured_output(self, schema, include_raw=False):
        outer = self

        class _S:
            async def ainvoke(self, prompt, **kw):
                outer.calls.append(("structured", prompt, kw))
                if outer.se:
                    raise outer.se
                return outer.sr

        return _S()


# ---------- 1. cfg → 客户端参数映射快照（不联网，构造期即定） ----------
def t1_payload_snapshot():
    g = LLMGateway(cfg=LLMConfig(model="gpt-4o", api_key="sk-test", max_token=2048,
                                 temperature=0.3, top_p=0.9, timeout=42, stream=False))
    m = g._model
    if m.model_name != "gpt-4o" or m.temperature != 0.3:
        _fail(f"1. 模型/温度没接上: {m.model_name} / {m.temperature}")
    if m.max_tokens != 2048:
        _fail(f"1. 源字段 max_token 未接进客户端 max_tokens: {m.max_tokens}")
    if float(m.request_timeout) != 42:
        _fail(f"1. timeout 未接上: {m.request_timeout}")
    if getattr(m, "top_p", None) != 0.9:
        _fail(f"1. 非默认 top_p 未条件注入: {getattr(m, 'top_p', None)}")
    # 默认值下不该硬塞 top_p/n/stop
    g2 = LLMGateway(cfg=LLMConfig(model="gpt-4o", api_key="sk-test"))
    if getattr(g2._model, "n", None) is not None:
        _fail("1. 默认 n=None 时不应把 n 传进客户端")


# ---------- 2. 未支持的 api_type 必须明确报错，不许静默退回 openai ----------
def t2_unsupported_api_type():
    for bad in (LLMType.ANTHROPIC, LLMType.BEDROCK, LLMType.QIANFAN, LLMType.SPARK, LLMType.ZHIPUAI, LLMType.ARK):
        try:
            LLMGateway(cfg=LLMConfig(api_type=bad, model="x", api_key="k"))
        except NotImplementedError as e:
            if bad.value not in str(e):
                _fail(f"2. 报错信息没带上 {bad.value}: {e}")
        else:
            _fail(f"2. api_type={bad.value} 竟然静默建了 ChatOpenAI —— S10 前必须显式失败")


# ---------- 3. format_msg 归一（源公开面） ----------
def t3_format_msg():
    g = _gw()
    out = g.format_msg([{"role": "system", "content": "s"}, HumanMessage(content="h"), "plain",
                        {"role": "assistant", "content": "a"}])
    kinds = [type(m).__name__ for m in out]
    if kinds != ["SystemMessage", "HumanMessage", "HumanMessage", "AIMessage"]:
        _fail(f"3. format_msg 归一结果错: {kinds}")
    if not all(isinstance(m, BaseMessage) for m in out):
        _fail("3. format_msg 必须全返回 BaseMessage")
    if len(g.format_msg("just a string")) != 1:
        _fail("3. format_msg(str) 应为单条 HumanMessage")


# ---------- 4. 计数单点：aask 一次只记一次账（stream 亦然） ----------
def t4_single_accounting():
    g = _gw()
    asyncio.run(g.aask("你好", tag="demo"))
    if len(g.cost_manager.records) != 1:
        _fail(f"4. aask 一次却记了 {len(g.cost_manager.records)} 笔 —— 出现重复计数")
    c = g.cost_manager.get_costs()
    if c.total_prompt_tokens <= 0 or c.total_completion_tokens <= 0 or c.total_cost <= 0:
        _fail(f"4. 三字段必须都非零: {c}")
    # 结构化路径不得再记一笔
    g2 = _gw(structured_result=None)
    if len(g2.cost_manager.records) != 0:
        _fail("4. 未调用却有账")
    # stream 同样只计一次，且逐片段回调 log_llm_stream（打字机链路）
    from codeharness import logs as _logs
    seen = []
    orig = _logs._llm_stream_log
    _logs.set_llm_stream_logfunc(seen.append)
    try:
        g3 = _gw(reply="abc")
        txt = asyncio.run(g3.aask("x", stream=True))
    finally:
        _logs.set_llm_stream_logfunc(orig)
    if txt != "abc" or len(g3.cost_manager.records) != 1:
        _fail(f"4. stream 计数或拼接异常: {txt!r} / {len(g3.cost_manager.records)} 笔")
    if seen != ["a", "b", "c", "\n"]:
        _fail(f"4. 打字机回调未按片段发射: {seen!r}")
    # calc_usage=False 应完全不记
    g4 = _gw(cfg=LLMConfig(model="gpt-4o", api_key="sk-test", calc_usage=False))
    asyncio.run(g4.aask("x"))
    if g4.cost_manager.records:
        _fail("4. calc_usage=False 时仍在记账")


# ---------- 5. FakeLLM 也必须记账（源曾在此漏记） ----------
def t5_fake_llm_accounts():
    f = FakeLLM(responses=["hi"])
    asyncio.run(f.aask("问", tag="t"))
    c = f.cost_manager.get_costs()
    if not (c.total_prompt_tokens and c.total_completion_tokens and c.total_cost):
        _fail(f"5. FakeLLM 未记账: {c}")
    # aask 必须经 ainvoke，否则又回到不记账的老路
    if not f.calls:
        _fail("5. FakeLLM.aask 没走 ainvoke")


# ---------- 6. 源 repair 符号面必须齐备且签名兼容 ----------
def t6_source_symbol_surface():
    import inspect
    from codeharness.provider import repair as R
    expect = {
        "RepairType": (), "repair_case_sensitivity": ("output", "req_key"),
        "repair_special_character_missing": ("output", "req_key"),
        "repair_required_key_pair_missing": ("output", "req_key"),
        "repair_json_format": ("output",), "_repair_llm_raw_output": ("output", "req_key", "repair_type"),
        "repair_llm_raw_output": ("output", "req_keys"), "repair_invalid_json": ("output", "error"),
        "run_after_exp_and_passon_next_retry": ("logger",), "repair_stop_after_attempt": ("retry_state",),
        "retry_parse_json_text": ("output",), "extract_content_from_output": ("content", "right_key"),
        "extract_state_value_from_output": ("content",), "repair_escape_error": ("commands",),
    }
    for name, params in expect.items():
        fn = getattr(R, name, None)
        if fn is None:
            _fail(f"6. 缺源符号 {name}")
        if params:
            got = [p for p in inspect.signature(fn).parameters if p not in ("kwargs",)]
            if got[: len(params)] != list(params):
                _fail(f"6. {name} 签名与源不符: {got} 应以前缀 {list(params)} 开头")
    # S6 逐字复制会调 repair_llm_raw_output(output, req_keys)
    fixed = R.repair_llm_raw_output('[CONTENT] {"a": 1} [CONTENT]', req_keys=["[/CONTENT]"])
    if "[/CONTENT]" not in fixed:
        _fail(f"6. repair_llm_raw_output 没补上右键: {fixed!r}")


# ---------- 7. repair_to_model 的组合档（历史漏修点） ----------
class Out(BaseModel):
    thought: str = ""


def t7_repair_combinations():
    from codeharness.provider.repair import repair_to_model
    cases = {
        "裸 JSON": '{"thought": "a"}',
        "只有围栏": '```json\n{"thought": "b"}\n```',
        "只有尾逗号": '{"thought": "c",}',
        "围栏+尾逗号（组合档）": '```json\n{"thought": "d",}\n```',
        "前后有废话": '好的，结果如下：\n```json\n{"thought": "e"}\n```\n希望有用',
        "带注释（repair_json_format 档）": '{"thought": "f"  # 说明\n}',
    }
    for label, raw in cases.items():
        got = repair_to_model(raw, Out)
        if got is None:
            _fail(f"7. {label} 修复失败: {raw!r}")
        if not got.thought.strip():
            _fail(f"7. {label} 解出来了但字段空: {got!r}")


# ---------- 8. retry_parse_json_text：CustomDecoder 的真实能力边界 ----------
def t8_retry_parse():
    from codeharness.provider.repair import repair_to_model, retry_parse_json_text
    from tenacity import RetryError

    # CustomDecoder(strict=False) 实测能吃：单引号、"": '' 这类非严格写法
    for raw in ("{'thought': 'x'}", '{"thought": ""}', '{"thought": \'y\'}'):
        if not isinstance(retry_parse_json_text(output=raw), dict):
            _fail(f"8. 非严格引号未解: {raw!r}")

    # 实测**吃不下尾逗号** —— 这正是新栈 repair_to_model 要补组合档的原因（源链里没有这一环）
    trailing = '{"thought": "z", }'
    try:
        retry_parse_json_text(output=trailing)
    except (RetryError, Exception) as e:
        if not isinstance(e, RetryError):
            _fail(f"8. 尾逗号应当失败（记录能力边界），实际异常类型 {type(e).__name__}")
    else:
        _fail("8. CustomDecoder 竟然解出了尾逗号——能力面变了，请同步改本文件与 repair 的档位设计")
    if repair_to_model(trailing, Out) is None:
        _fail("8. repair_to_model 必须补上源链缺的这一档（尾逗号）")

    # 两档重试环生效的证据：缺逗号的 JSON 被 after 钩子里的 repair_invalid_json 补好
    # ⚠ 必须用 output= 关键字调用——after 钩子写回的是 retry_state.kwargs["output"]，
    #    位置传参时它改了 kwargs 而实参没变，第二圈仍在重解原始坏文本（源即有此坑，已在 docstring 标注）
    broken = '{\n"a": 1\n"b": 2\n}'
    got = retry_parse_json_text(output=broken)
    if not isinstance(got, dict) or set(got) != {"a", "b"}:
        _fail(f"8. repair_invalid_json 重试环没把缺逗号的 JSON 修好: {got!r}")

    # 完全不是 JSON 的输入：重试耗尽必须抛出，不许返回垃圾
    from codeharness.configs.settings import settings as _s
    _s.repair_llm_output = False          # 关掉修复则只重试一次，避免白等两秒
    try:
        try:
            retry_parse_json_text(output="这不是 JSON，只是一段中文说明")
        except RetryError:
            pass
        else:
            _fail("8. 不可修复的输入竟然成功了")
    finally:
        _s.repair_llm_output = True


# ---------- 9. extract_* 与 escape 修复 ----------
def t9_extract_helpers():
    from codeharness.provider.repair import (extract_content_from_output,
                                             extract_state_value_from_output, repair_escape_error)
    if extract_content_from_output("[CONTENT]\n{\"a\": 1}\n[/CONTENT]") != '{"a": 1}':
        _fail(f"9. extract_content_from_output: {extract_content_from_output('[CONTENT]{\"a\":1}[/CONTENT]')!r}")
    if extract_state_value_from_output("我选择 2 号方案") != "2":
        _fail("9. extract_state_value_from_output 没提取出状态号")
    if extract_state_value_from_output("没有数字") != "-1":
        _fail("9. 无数字时应回落 -1")
    if "\\\\f" not in repair_escape_error("a\fb"):
        _fail(f"9. repair_escape_error 没转义 \\f: {repair_escape_error('a\fb')!r}")


# ---------- 10. 配置：字段名照源 + env 注入 + redis to_url ----------
def t10_settings():
    if "max_token" not in LLMConfig.model_fields or "max_tokens" in LLMConfig.model_fields:
        _fail("10. 源字段名是 max_token（单数），必须一致，否则逐字复制的取不到属性")
    if LLMConfig(max_token=-1).max_token != 4096:
        _fail("10. max_token 非正值未回落 4096")
    for f in ("api_type", "api_version", "pricing_plan", "access_key", "secret_key", "session_token",
              "endpoint", "app_id", "api_secret", "domain", "region_name", "top_p", "top_k", "stream",
              "timeout", "context_length", "proxy", "calc_usage", "compress_type", "use_system_prompt",
              "reasoning", "reasoning_max_token"):
        if f not in LLMConfig.model_fields:
            _fail(f"10. LLMConfig 缺源字段 {f}")
    if LLMType("openai") is not LLMType.OPENAI or len(LLMType) < 27:
        _fail(f"10. LLMType 成员数不对: {len(LLMType)}")
    if RedisConfig(host="h", port=1).to_url() != "redis://h:1/0":
        _fail(f"10. redis to_url: {RedisConfig(host='h', port=1).to_url()}")
    if RedisConfig(ssl=True).to_url().split(":")[0] != "rediss":
        _fail("10. ssl=True 应出 rediss://")
    # .env 的双下划线注入通路（用临时实例，不改全局 settings）
    import os
    os.environ["LLM__MAX_TOKEN"] = "777"
    try:
        if Settings().llm.max_token != 777:
            _fail("10. LLM__MAX_TOKEN 未能注入嵌套配置")
    finally:
        os.environ.pop("LLM__MAX_TOKEN", None)
    if not hasattr(settings, "repair_llm_output"):
        _fail("10. settings 缺 repair_llm_output 开关（repair 层读它）")


# ---------- 11. 计量：只读累计；预算强制不得回潮 ----------
def t11_usage():
    cm = CostManager()
    cm.update_cost(1000, 1000, "gpt-4o")
    if cm.total_prompt_tokens != 1000 or cm.total_completion_tokens != 1000 or cm.total_cost <= 0:
        _fail(f"11. update_cost 累计不对: {cm.get_costs()}")
    c = cm.get_costs()
    if len(c) != 3:
        _fail(f"11. Costs 应只有 pt/ct/cost 三个只读字段: {c}")
    # 未知模型：记 token 但不算钱（价目表缺项不得污染成本）
    cm2 = CostManager()
    cm2.update_cost(10, 10, "no-such-model-xyz")
    if cm2.total_prompt_tokens != 10 or cm2.total_cost != 0:
        _fail(f"11. 未知模型应记 token 不计成本: {cm2.get_costs()}")
    # 免费模型走 TokenCostManager
    cm3 = TokenCostManager()
    cm3.update_cost(5, 5, "gpt-4o")
    if cm3.total_cost != 0 or cm3.total_prompt_tokens != 5:
        _fail("11. TokenCostManager 不该算钱")
    # 预算强制已作废：这些符号存在即为回潮
    for name in ("max_budget", "total_budget", "check_budget", "is_within_budget", "update_budget"):
        if hasattr(CostManager, name) or name in CostManager.model_fields:
            _fail(f"11. 预算符号回潮: {name}")
    import codeharness.provider.cost as cost_mod
    import codeharness.provider.gateway as gw
    import codeharness.utils.common as common_mod
    for mod in (cost_mod, gw, common_mod):
        if hasattr(mod, "NoMoneyException"):
            _fail(f"11. NoMoneyException 回潮: {mod.__name__}")
    # 价目表单源：两份字典必然调价漏一处（注意别比实例字段——pydantic 会拷贝 dict 默认值）
    from codeharness.utils import token_counter
    if token_counter.TOKEN_COSTS is not cost_mod.TOKEN_COSTS:
        _fail("11. TOKEN_COSTS 出现第二真源")
    # 真模型流式必须带 stream_usage，否则末块无 usage、整条线账为 0（FakeLLM 自造 metadata 测不出）
    m = LLMGateway._build(LLMConfig(model="gpt-4o", api_key="sk-test", stream=True))
    if getattr(m, "stream_usage", None) is not True:
        _fail("11. 流式未开 stream_usage —— 真模型记账恒 0")
    m2 = LLMGateway._build(LLMConfig(model="gpt-4o", api_key="sk-test", stream=False))
    if getattr(m2, "streaming", False) or getattr(m2, "stream_usage", None):
        _fail("11. stream=False 却建成了流式模型")


# ---------- 12. structured 回落修复档 + aask_code ----------
def t12_structured_and_code():
    class S(BaseModel):
        thought: str = ""

    g = _gw(structured_error=ValueError("bad json"), reply="")
    g._model.se = ValueError('x')
    # 让 with_structured_output 抛错并带上可修复的原始文本
    class _Boom(Exception):
        def __init__(self, out):
            super().__init__("parse failed")
            self.output = out

    g._model.se = _Boom('```json\n{"thought": "z",}\n```')
    got = asyncio.run(g.structured(S).ainvoke("p"))
    if got.thought != "z":
        _fail(f"12. structured 失败后未走 repair_to_model: {got!r}")

    g2 = _gw(reply="说明\n```python\nprint(1)\n```\n完")
    code = asyncio.run(g2.aask_code("写个 hello", language="python"))
    if "print(1)" not in code:
        _fail(f"12. aask_code 没取出代码块: {code!r}")


def main():
    checks = [t1_payload_snapshot, t2_unsupported_api_type, t3_format_msg, t4_single_accounting,
              t5_fake_llm_accounts, t6_source_symbol_surface, t7_repair_combinations,
              t8_retry_parse, t9_extract_helpers, t10_settings, t11_usage, t12_structured_and_code]
    for c in checks:
        c()
        print(f"  ok  {c.__name__}")
    print(f"\nS2 门禁全部通过：{len(checks)} 组（cfg→客户端快照 / 未支持厂商显式失败 / format_msg / "
          f"计数单点 / FakeLLM 记账 / 源 repair 14 符号 / 组合修复档 / 两档重试环 / extract 系列 / "
          f"配置字段照源与 env 注入 / 只读计量与预算不回潮 / structured 回落与 aask_code）")


if __name__ == "__main__":
    main()
