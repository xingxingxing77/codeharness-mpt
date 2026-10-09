"""SessionRunner：会话 ↔ LangGraph 团队图。三件事：
1) 会话任务入口装七个 ContextVar（SESSION_ID/CURRENT_PROJECT/CURRENT_SESSION/REPORT_SINK/CHAT_SINK/…）——
   内核的报道与插话因此零依赖 web 层；`CURRENT_PROJECT` 是产物目录名、`CURRENT_SESSION` 是会话身份（③），
   两者刻意分开（见 `codeharness/runtime.py` 里 CURRENT_SESSION 的注释）；
2) astream_events 里只翻译两件事：LLM token（Thought 打字机——structured 的逐片 JSON 在这儿抽成
   散文再上屏，见 `_ProseStream`）与 interrupt（ask_human）；
   其余块事件全部来自内核报道槽（report.py），此处只做 sink→bus 转发；
3) 人工回答 = 同 graph 实例 + 同 thread_id 的 Command(resume)，且必须重装同一套 ContextVar。"""
import asyncio
import json
import time
import traceback
from contextlib import contextmanager
from codeharness.const import NO_STREAM_TAG
from codeharness.logs import logger
from langgraph.types import Command
from server.bridges import SESSION_ID
from server.sessions import Session, SessionStatus

LIVE_BLOCKS = ("Thought", "Docs", "Task")   # 能承载逐片正文的块：打字机就投进这一笔所在的那块
def _prompt_text(data: dict) -> str:
    """`on_chat_model_start` 事件里这一笔的输入文本，拼成一串给回显判定用。

    只在这一笔的存活期里持有（`on_chat_model_end` 即弃），封顶 `_ECHO_PROBE_CAP` 字符——
    prompt 通常几万字，而回显判定只需要「成员前缀是否原样出现在输入里」，取尾部更有用
    （用户需求一般在最后那条 HumanMessage 上）。
    """
    msgs = (data or {}).get("input")
    if msgs is None:
        msgs = (data or {}).get("messages")
    parts = []

    def walk(x):
        # 形状两种都见过，都得认：直连 chat model 的 `astream_events` 给扁平列表，
        # 而 structured 链走 `ainvoke` 时 langchain 发的是**批式嵌套**（`[[msg, …], …]`）。
        # 09-29 踩过：只认扁平 ⇒ 真会话里抽到空串 ⇒ 回显规则静默空转（离线复现现证）。
        if isinstance(x, str):
            parts.append(x)
        elif isinstance(x, (list, tuple)):
            for y in x:
                walk(y)
        elif isinstance(x, dict):
            # 真会话现证：`data["input"]` 是个 dict（键在 `messages`/`prompts` 之间漂），
            # 只认列表会抽到空串 ⇒ 回显规则静默空转（离线 SSE 桩复现，`type(input)=dict`）
            for y in x.values():
                walk(y)
        else:
            c = getattr(x, "content", None)
            if c is not None:
                walk(c)

    walk(msgs)
    return "".join(parts)[-_ECHO_PROBE_CAP:]


_ECHO_PROBE_CAP = 200_000
MIN_PROSE = 24          # structured 流里「算散文」的下限（字符）。09-29 活体现证过两头：
# 80 会把真散文挡在门外（一场 dynamic 跑的 thought 实测 64 字，一格没发），而 24 仍然把枚举值
# 与字段名关在外面（`REQUIREMENT`=11、`en`=2、`original_requirements`=21）。
# 长度只是**兜底**门槛：块若声明了 `prose_fields`（见 `codeharness/report.py` 的 `_meta_with_prose`），
# 门控按字段语义挑成员，长度门槛退到名单之内——因为「够长」挡不住键名本身（现证
# `data_structures_and_interfaces`=30、`competitive_quadrant_chart`=26 都长过 24，作为键名被打上屏）。
_KEY_CAP = 128        # 键名全长上限：超过就不是字段名，该成员按「名单外」处理（本仓最长的键 30 字）。

_HEX = "0123456789abcdefABCDEF"
_ESC = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_REPL = "\ufffd"


class _ProseStream:
    """structured 调用的逐片 JSON → 只发布其中达标的字符串成员（模型的真散文）。

    为什么在翻译层抽：内核拿不到 token 流——`structured()` 走 `runnable.ainvoke`（C17 钉死的
    usage 记账口径，换 astream 逐块累加会把 usage 与截断信号丢掉），而 `astream_events` 的
    `on_chat_model_stream` 本来就把逐片 JSON 送到这里。改之前这一路是**原样上屏**：09-28 实测
    一跑 956 片、15177 字的 `{\n  "language": "en"...`，而正文要等内核解析完才整段落地——
    用户报的「不是流式输出」+「一大块文本出现」正是这两下。

    三态：0 未定型（攒首部空白，裸文本要整段还回去）/ 1 JSON / 2 裸文本原样透传（＝改动前行为）。
    JSON 模式不建语法树，只认字符串成员并解转义（含 `\\uXXXX` 与被切片切断的代理对）。

    挑选有两道：`only`（块声明的正文字段名单，按**键名**门控）与 `MIN_PROSE`（长度兜底）。名单靠
    「字符串闭合后紧跟冒号 ⇒ 刚才是键名」认出来，不建语法树；`only=None` 时整套键名跟踪都不启动，
    行为与只有长度门槛那一版逐字相同（没声明名单的 schema 一个字节都不变）。回显字段靠**这一笔的
    prompt 文本**排（`_prompt_text`），值原样出现在输入里的那种（`original_requirements` 抄用户的话）不发。
    """

    __slots__ = ("mode", "head", "in_str", "phase", "ubuf", "hi", "decoded", "locked", "emitted",
                 "echo", "skipping", "echoed", "only", "key", "buf", "pending",
                 "stack", "val_expected", "is_key")

    def __init__(self, echo: str = "", only=None):
        # 名单缺失＝不门控。frozenset 而非 list：`in` 的语义是「这个字段名在不在名单里」，不看顺序。
        self.only = frozenset(only) if only is not None else None
        self.key = None           # 当前值所属的键名（最近一个后跟冒号的字符串）
        self.buf = ""             # 当前字符串的全长文本，只在判定它是键名时用
        self.pending = False      # 刚闭合一个字符串，还没看见后面第一个非空白字符
        # C129：键名/值位的区分器。改前「这串是不是键名」要等闭合后见冒号才定，而字符在串**开着**
        # 的当下就逐个过 _emit——门控拿的是「上一个成员的键」：上一个键在名单内时，紧跟着的
        # 名单外键名（PRDOutput 的 competitive_analysis → 26 字 competitive_quadrant_chart 恰是
        # 这条相邻关系）攒满 MIN_PROSE 就整条漏上屏。现在维护一层括号栈 + 「在等值」位，
        # 串开的一刻就能定它是不是键名：栈顶是对象且没在等值 ⇒ 键名，键名字符只进 buf 比对、不上屏。
        self.stack = []           # 开着的容器栈（"{" / "["），只在 only 门控启用时维护
        self.val_expected = False # 刚见过冒号 ⇒ 下一个串是值
        self.is_key = False       # 当前开着的字符串是键名
        self.echo = echo or ""    # 这笔调用的 prompt 文本：成员前缀命中它 ⇒ 是回显，不是模型的话
        self.skipping = False     # 当前成员已判定为回显，整段丢掉
        self.echoed = 0           # 被判定为回显而丢掉的成员数（判据要数这个，不看日志）
        self.mode = 0
        self.head = ""            # 未定型期攒的分片（裸文本要整段还回去，不能吞首部空白）
        self.in_str = False
        self.phase = 0            # 0 正常 / 1 刚见反斜杠 / 2 正在攒 \\u 的十六进制
        self.ubuf = ""
        self.hi = None            # 攒着的高位代理（代理对被切断时跨片合回来）
        self.decoded = ""         # 未达标前攒的已解文本
        self.locked = False       # 已达标，开始逐片外发
        self.emitted = False      # 本笔已发过成员（决定要不要补换行分隔）

    def feed(self, text: str) -> str:
        """喂一片 `chunk.content`，回应当发布的片段（可能是空串）。"""
        if not text:
            return ""
        if self.mode == 2:
            return text
        if self.mode == 0:
            self.head += text
            probe = self.head.lstrip()
            if not probe:
                return ""                     # 全是空白，还没资格定型
            self.mode = 1 if probe[0] == "{" else 2
            text, self.head = self.head, ""
            if self.mode == 2:
                return text
        out = []
        for ch in text:
            if not self.in_str:
                if ch == '"':
                    self.in_str = True
                    if self.only is not None:
                        self.buf = ""
                        # C129：这串是键名还是值，在**开**的一刻就定（见 __init__ 的注释）。
                        self.is_key = bool(self.stack) and self.stack[-1] == "{" and not self.val_expected
                    continue
                if ch in " \t\r\n":
                    continue
                # 容器与分隔符（C129 新增的栈跟踪；改前只认 `"` 与 pending 后的冒号）。
                # pending 一并就地清：闭合串后面跟着 `{`/`[`/`}`/`]`/`,` 都等于「它是个值」。
                if ch == "{":
                    self.stack.append("{")
                    self.val_expected = self.pending = False
                elif ch == "[":
                    self.stack.append("[")
                    self.val_expected = self.pending = False
                elif ch in "}]":
                    if self.stack:
                        self.stack.pop()
                    self.val_expected = self.pending = False
                elif ch == ":":
                    if self.pending:
                        self.key = self.buf
                    self.val_expected, self.pending = True, False
                elif ch == ",":
                    self.val_expected = self.pending = False
                else:
                    self.pending = False     # 裸 token（true/1/null…）：维持原有「见非空白即清」的口径
                continue
            if self.phase == 1:
                self.phase = 0
                if ch == "u":
                    self.phase, self.ubuf = 2, ""
                    continue
                self._emit(_ESC.get(ch, ch), out)
                continue
            if self.phase == 2:
                if ch not in _HEX:
                    self.phase, self.ubuf = 0, ""
                    self._emit(ch, out)       # 非法转义：按字面收
                    continue
                self.ubuf += ch
                if len(self.ubuf) < 4:
                    continue
                v, self.ubuf, self.phase = int(self.ubuf, 16), "", 0
                self._emit(self._code(v), out)
                continue
            if ch == "\\":
                self.phase = 1
                continue
            if ch == '"':
                self.in_str = self.locked = self.skipping = False
                self.decoded = ""             # 达标与否都从头找下一个成员
                if self.only is not None:
                    self.pending = True       # 是键名还是值，等下一个非空白字符说话
                continue
            self._emit(ch, out)
        return "".join(out)

    def _code(self, v: int) -> str:
        if 0xD800 <= v <= 0xDBFF:
            self.hi = v
            return ""
        if 0xDC00 <= v <= 0xDFFF:
            if self.hi is not None:
                hi, self.hi = self.hi, None
                return chr(0x10000 + ((hi - 0xD800) << 10) + (v - 0xDC00))
            return _REPL
        if self.hi is not None:
            self.hi = None
            return _REPL
        return chr(v)

    def _emit(self, ch: str, out: list):
        if not ch or self.skipping:
            return
        if self.only is not None:
            if len(self.buf) < _KEY_CAP:
                self.buf += ch        # 键名要全长才能比对，值也要走这一格（超限只影响键名判定）
            if self.is_key:
                return                # C129：键名字符只进 buf，永远不上屏（开串那一刻已定键位/值位）
            if self.key not in self.only:
                return                # 名单外的字段：一个字都不发，等定稿一次性出现
        if self.locked:
            out.append(ch)
            return
        self.decoded += ch
        if len(self.decoded) < MIN_PROSE:
            return
        cand, self.decoded = self.decoded, ""
        # 回显排除：达标前缀原样出现在这笔的 prompt 里 ⇒ 那是模型把用户的需求抄了一遍，
        # 09-29 经典线活体现证它在逐片最前面（`做一个极小的静态网页，用一段话解释二分查找…`）。
        # 整段跳过、继续找下一个成员；判定只看一次（前缀命中即定，不逐字再判）。
        if self.echo and cand in self.echo:
            self.skipping, self.echoed = True, self.echoed + 1
            return
        self.locked = True
        if self.emitted:
            out.append("\n")                  # 成员之间给一个可见分隔
        self.emitted = True
        out.append(cand)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _seeded_ledger(saved: dict):
    """进程重启后的 resume 路径：账本从会话记录里上次落盘的快照起算。
    不播种的话新账本从 0 起，终态快照会把历史用量整段覆盖掉（合流断点之二）。

    ⚠ 只读 `cost_usd`/`cost_cny` 两个键。老记录里那个单一 `total_cost` 是**混币种脏数**
    （美元行与人民币行加在同一个 float 上），C12 拍板不留兼容字段，所以这里直接不读——
    读它等于把错口径继续带进新快照。

    C78（09-28 审查批）：三个**观测计数**（`truncated_calls`/`unknown_command_calls`/
    `empty_output_calls`）也必须播种——它们在 `cost_snapshot` 里是被持久化、被 `/api/sessions`
    带出去的字段（C19 落下来的「无效调用」观测就靠这三个数），而新账本从 0 起 ⇒ 重启后 resume
    老会话，终态快照会把历史值**静默覆盖成 0**，且 0 是合法读数、看不出是丢的。
    C122 加到四个：`invalid_args_calls`（args 没过工具 args_schema 的回喂笔数）同规。
    """
    from codeharness.provider.cost import CostManager
    cm = CostManager()
    cm.total_prompt_tokens = int(saved.get("total_prompt_tokens", 0) or 0)
    cm.total_completion_tokens = int(saved.get("total_completion_tokens", 0) or 0)
    cm.cost_usd = float(saved.get("cost_usd", 0) or 0)
    cm.cost_cny = float(saved.get("cost_cny", 0) or 0)
    # C78：三个观测计数一并播种（理由见 docstring）。缺键/坏值一律退 0，老记录不受影响。
    cm.truncated_calls = int(saved.get("truncated_calls", 0) or 0)
    cm.unknown_command_calls = int(saved.get("unknown_command_calls", 0) or 0)
    cm.empty_output_calls = int(saved.get("empty_output_calls", 0) or 0)
    # C122：第四个无效调用计数同规播种（args 没过工具 args_schema 的回喂笔数）
    cm.invalid_args_calls = int(saved.get("invalid_args_calls", 0) or 0)
    # R1：召回链那三个也同规播种——它们一样是被 `/api/sessions` 带出去的字段，
    # 漏一个就等于跨重启把「这场召不回过几次」抹成 0（0 是合法读数，抹掉看不出来）。
    cm.recall_failures = int(saved.get("recall_failures", 0) or 0)
    cm.recall_zero_hits = int(saved.get("recall_zero_hits", 0) or 0)
    cm.recall_returned = int(saved.get("recall_returned", 0) or 0)
    # R4：写腿那两笔同规（点数与次数成对，缺前者就分不清「没失败」与「压根没写过」）
    cm.overflow_failed = int(saved.get("overflow_failed", 0) or 0)
    cm.overflow_written = int(saved.get("overflow_written", 0) or 0)
    # C184：窗口占用三笔同规播种（last/peak/system，口径见 CostManager 字段注释）。
    # 漏播种的形状：resume 后「本轮峰值」从 0 重算、卡片的末笔/峰值静默缩水——0 是合法读数，看不出是丢的。
    cm.last_prompt_tokens = int(saved.get("last_prompt_tokens", 0) or 0)
    cm.peak_prompt_tokens = int(saved.get("peak_prompt_tokens", 0) or 0)
    cm.last_system_tokens = int(saved.get("last_system_tokens", 0) or 0)
    # C185（P4）：跨阶段事实清单的单独计数同规播种（漏了＝resume 后卡片那一行静默归 0）。
    cm.last_facts_tokens = int(saved.get("last_facts_tokens", 0) or 0)
    # P0：校准契约两键 + 静默丢失计数同规播种。k 缺省退 **1.0** 不是 0——0 不在合法域（[0.5,4]）里；
    # `calibration_model` 漏播种会让 resume 后第一笔校准读数把 "" 误判成「换模型」而重置。
    cm.calibration_k = float(saved.get("calibration_k") or 1.0)
    cm.calibration_model = str(saved.get("calibration_model") or "")
    cm.silent_lost = int(saved.get("silent_lost", 0) or 0)
    return cm


def cost_snapshot(cm) -> dict:
    """成本快照的唯一出口（`_sync_cost` 与 `_publish_status` 两处必须同形，
    否则 SSE 那一路与 GET 那一路读到的字段集会漂）。币种分两桶，**不相加**。"""
    c = cm.get_costs()
    # 两个「无效调用」计数不住在 `Costs` 里（那是钱与 token 的计量口径，字段集有结构守卫钉着），
    # 它们是 manager 上的观测项；快照这份 dict 才是被持久化、被列表出口带出去的那一份。
    return {"cost_usd": round(c.cost_usd, 6), "cost_cny": round(c.cost_cny, 6),
            "total_prompt_tokens": c.total_prompt_tokens,
            "total_completion_tokens": c.total_completion_tokens,
            "truncated_calls": getattr(cm, "truncated_calls", 0),
            "unknown_command_calls": getattr(cm, "unknown_command_calls", 0),
            "empty_output_calls": getattr(cm, "empty_output_calls", 0),
            "invalid_args_calls": getattr(cm, "invalid_args_calls", 0),
            # R1：召回链观测（成功率 = returned/(returned+zero_hits)，可用性看 failures）。
            "recall_failures": getattr(cm, "recall_failures", 0),
            "recall_zero_hits": getattr(cm, "recall_zero_hits", 0),
            "recall_returned": getattr(cm, "recall_returned", 0),
            "overflow_failed": getattr(cm, "overflow_failed", 0),
            "overflow_written": getattr(cm, "overflow_written", 0),
            # C184：窗口占用三笔（pt 是单发占用＝窗口口径，不是累计；见 CostManager 字段注释）
            "last_prompt_tokens": getattr(cm, "last_prompt_tokens", 0),
            "peak_prompt_tokens": getattr(cm, "peak_prompt_tokens", 0),
            "last_system_tokens": getattr(cm, "last_system_tokens", 0),
            # C185（P4）：跨阶段事实清单的单独计数（0 = 这一场还没注入过，不是「注入了 0 字」）
            "last_facts_tokens": getattr(cm, "last_facts_tokens", 0),
            # P0：校准契约两键 + 静默丢失计数（k 缺省 1.0 不是 0——0 不在 [0.5,4] 合法域里）
            "calibration_k": getattr(cm, "calibration_k", 1.0),
            "calibration_model": getattr(cm, "calibration_model", ""),
            "silent_lost": getattr(cm, "silent_lost", 0)}


class SessionRunner:
    def __init__(self, store, bus, chat_factory=None):
        """⚠ 这里**不再收 `llm_defaults`**：它从建类起就是个装死参数（`__init__` 收下、类体里没有
        任何一处赋给 self，真消费者读的是 `app.state.llm_defaults`）。「看起来有条通路其实没人接」
        与 `metadata.writes` 死字段同族，而它比那个更容易骗人：`app.py` 确实在传，读代码的人
        会以为会话级的模型默认值是从这儿进 runner 的。（调用点全部是关键字或两个位置参数，删掉安全。）"""
        self.store, self.bus = store, bus
        self.tasks: dict[str, asyncio.Task] = {}
        self.graphs: dict[str, tuple] = {}        # sid -> (graph, config)——resume 用
        self.chats: dict[str, object] = {}        # sid -> ChatQueue
        self.costs: dict[str, object] = {}        # sid -> CostManager（图内共用的那一个账本）
        self.projects: dict[str, str] = {}        # sid -> 产物目录名
        self._closers: set = set()                # 散会收壳的后台任务，握住引用防被 GC 半路回收
        self._ck = None                            # 进程级 checkpointer（懒建）
        # S7 双实现注入点（默认全 None=进程内旧路，feature flag 在 lifespan 装配）：
        self.chat_factory = chat_factory           # (sid) -> ChatQueue 同接口对象（Redis LIST 版）
        self.trace = None                          # platforms.trace.TraceStore；on_chat_model_end 记 span
        self._ctl = None                           # 跨 worker stop 的发布端（redis 模式才有）
        self._ctl_task = None
        self._last_span: dict[str, tuple] = {}     # sid -> (pt, ct, cost_usd, cost_cny) 上次累计值，span 取增量
        self._trunc_reported: dict[str, int] = {}   # sid -> 已报过的截断笔数（B8），防长跑 resume 刷同一条提示
        # (sid, run_id) -> [派发时刻, 首 token 时刻]。键用 run_id 不用节点名：同一节点在一跑里
        # 会被调多次（n_round 循环），按节点名会把复用/并发的调用串成一条。
        self._call_t0: dict[tuple[str, str], list] = {}
        # (sid, run_id) -> 该笔调用的散文抽取器。同样按 run_id 不按节点名：一笔一个状态机，
        # 收口（on_chat_model_end）即弃，否则跨笔调用会把上一笔的成员进度带过来。
        self._prose: dict[tuple[str, str], _ProseStream] = {}
        self._prompts: dict[tuple[str, str], str] = {}   # (sid, run_id) -> 这笔的输入文本（回显判定用）
        self._used_kernel_block: dict[tuple[str, str], bool] = {}   # (sid, run_id) -> 这一笔是否落进了内核块
        # C177：(sid, run_id) -> 这一笔由**调用方**声明了「不上打字机行」（`const.NO_STREAM_TAG`，
        # 产出是要落盘的代码正文那五处 `_aask`）。记账与 span 照旧，挡的只有上屏；
        # 收口那一步要认它，否则兜底行会被这一笔无端关掉（同一节点里另一笔还开着）。
        self._silent_runs: dict[tuple[str, str], bool] = {}
        # (sid, 外层节点名=角色) -> 这一笔 LLM 调用的逐片该投进哪一块：那个角色手上**最后开着且未收口**的块
        # （`Thought`/`Docs`/`Task`，值带它的 block 名），没有就由 `_translate` 落 `stream-{node}` 兜底。
        # 于是文档块自己就在流：逐片进 `live`，内核定稿（`content`）一到前端把 `live` 整段撤掉。
        self._live_blk: dict[str, tuple] = {}
        # C130：每笔 LLM 调用开跑那一刻的落点快照（(sid, run_id) → `_live_blk[sid]` 当时值）。
        # 并行 Send（一条消息路由多个角色）下两个内核块并发开：改前逐片投递实时读 `_live_blk[sid]`
        # 这个**每会话单槽**，后开的块把登记覆盖掉，前一个角色的散文就投进别人块的 uuid（事件
        # role 还是自己的节点名，前端拼出串色块）。快照把「这一笔属于哪块」定死在 start 时刻——
        # 「meta 先于这一笔首片到达」的既有前提不变，只是把读取点从「首片」提前到「start」。
        # `_live_blk` 本身保留「当前开着」语义：end_marker 配对与 start 的静默期判定还靠它。
        self._blk_of: dict[tuple[str, str], tuple] = {}
        # C174：上面的槽改键成 (sid, owner) 之前的形状是「每会话一个槽」，桩 ⑩ 现证过它的后果——
        # Alice 的块还开着时她的第二笔（同一个 thought_block 里本就有 structured/aask/repair 三笔）
        # 恰好被 Bob 后开的块顶掉槽 ⇒ 逐片整把投进 Bob 的卡。owner 取外层节点名，两侧同源（见 `_owner`）。
        # C172：sid -> {兜底行 uuid `stream-{node}`: 这一行有没有真收到过逐片}。
        # 兜底行的收口标记**只**挂在 `on_chat_model_end` 上，而中断的调用压根走不到那儿——下面
        # `_forget` 里那句「中断的调用不会走到 on_chat_model_end，在途表必须在这里扫干净」早就认了
        # 这件事，只是当时只扫了在途表，**没扫事件流**：那块在流里保持开着，前端三处只认
        # `b.closed`（Think 行停在 running 扫光、Docs 的 mermaid 不水合、轮尾行不发＝分叉入口也没了），
        # 且冷档回放同形。这张表就是「散会时还有谁没关」的账，值用来决定要不要补分隔（C173）。
        self._stream_rows: dict[str, dict[str, bool]] = {}
        # C190（10-10）：角色生命周期（车道）的三张表。形状是现取的，不是照参照系 `run_started` 想象的
        # ——`tests/manual_role_node_shape_probe.py`（真图 + 本机 FakeLLM，零花费）三条：
        #   · 角色节点本体 = `name == metadata.langgraph_node == 角色名` 且 `checkpoint_ns` 为空；
        #     内层 think/gate/act/observe 的 `checkpoint_ns` 首段才是角色名（`_owner` 一直靠它）；
        #   · ⚠ **整场所有节点级事件的 `run_id` 是同一个**（现证 48 条 `on_chain_*` 全是同一颗
        #     `01a1215d…`）⇒ 它当不了「这一趟执行」的标识。注意这与 `on_chat_model_*` 那侧不同：
        #     那边每笔调用一个 run_id，`_call_t0` 就是那么用的——照它推就会把两次激活并成一条；
        #   · `graph:step:N` 也配不上对（本体 start 在 step:2、end 在 step:6，中间夹着别的激活）。
        # 所以配对只能自己攒：开一条车道压一颗 uuid、收口弹一颗（`_lane_open` 是**按角色的栈**）。
        # `_lane_t0` 给「这一趟几秒」，`_lane_seq` 只管 uuid 唯一，`_node_names` 是装配出口那份名单
        # （与 `/chat` 的目标校验同源，不另造一份角色名单）。
        # ponytail: 同角色并发重入（dynamic 线把两条 Send 投给同一个成员）时，LIFO 会把后一趟的 ms
        # 记到前一条车道上——车道文本不受影响，只有「用时」那个数会串。升级路径=用 `checkpoint_ns`
        # 里那颗每次激活都换的 uuid 当配对键（现证两次激活是 `172ef58a…` 与 `8cbd625c…` 不同值），
        # 代价是车道的 uuid 要改到本体 start 之后才发得出来。今天没有消费者报这个痛。
        self._node_names: dict[str, set] = {}
        self._lane_open: dict[str, dict] = {}
        self._lane_t0: dict[tuple[str, str], float] = {}
        self._lane_seq: dict[str, int] = {}

    # ---- 生命周期（契约：start/stop） --------------------------------------
    def _spawn(self, sid: str, coro):
        """两条 fire-and-forget 起跑（`start` 与 `answer_human`）的**唯一咽喉**。

        为什么要有这一处：跑图的协程在收口时都要往 store 落一次态，而**会话行可以在起跑之后消失**——
        `DELETE /{sid}` 的 409 只挡 running/stopping/awaiting_human，所以「给一场没在跑的会话回一张
        审批卡、再把它删掉」正好撞在起跑与落态之间（s8 门禁的 t8 现证过这个形状：28/28 全绿之后吊出
        `Task exception was never retrieved: KeyError`，栈停在 `_resume` 落 running 那一句）。
        `store.update` 对不存在的 sid 抛 KeyError（`platforms/session_store.py:116`），而这任务从没被人
        await ⇒ 异常无人 retrieve＝日志里零痕迹；`_resume` 那条还多一坏：`tasks.pop` 写在 try 的
        `finally` 里，而抛穿发生在 try **之前** ⇒ 死任务永久占住 `tasks[sid]`（:559 那两条注释讲的
        正是抢槽的代价）。现在两处一起收在这一个咽喉：认「会话已不在」⇒ 可 grep 的 warning + 散会；
        槽按**任务对象自己**摘，不盲摘 sid（免得替别人清了槽）。"""
        async def guarded():
            try:
                await coro
            except KeyError as e:
                # C125（10-02 复审批）：KeyError ≠ 「行已不在」。`_resume` 的 `_ensure_graph`（含
                # `_prepare` 装配：未知 SOP 模板、角色缺 name 都在这抛）在它自己的 try **之外**，
                # 这里的 KeyError 发生时会话行明明还在——改前被误判成「散会」：打假告警、不发
                # error 事件、状态钉死 awaiting_human，再答一次同样炸。先核行在不在再选分支；
                # 行还在就是真崩溃，交回 `_fail`（C117 守卫 + C116 kind 分类照常生效）。
                if self.store.get(sid) is None:
                    logger.warning(f"[runner-dropped] sid={sid} 落态时会话行已不在（{e}），本场按散会收")
                    self._forget(sid, terminal=True)
                else:
                    self._fail(sid, e)
            finally:
                if self.tasks.get(sid) is asyncio.current_task():
                    self.tasks.pop(sid, None)
        self.tasks[sid] = asyncio.create_task(guarded())

    def start(self, session: Session):
        if self.is_running(session.id):
            raise RuntimeError(f"session {session.id} already running")
        self._spawn(session.id, self._run(session))

    def is_running(self, sid: str) -> bool:
        t = self.tasks.get(sid)
        return t is not None and not t.done()

    async def stop(self, sid: str) -> bool:
        t = self.tasks.get(sid)
        if t and not t.done():
            self.store.update(sid, status=SessionStatus.stopping)
            t.cancel()
            return True
        # 本 worker 没有这个 task：会话可能跑在别的 worker 上（多进程部署）。
        # 控制通道 PUBLISH ch:ctl，持任务的那个 worker 收到自己 cancel（施工4 目标结构第 7 行）。
        # ⚠ 只在 store 说「可能在跑」时才转发——否则对 created/finished 会话点停止会把它
        # 误标 stopping 卡死（多 worker 冒烟实测：stopped:true 但没人会来收尾）。
        s = self.store.get(sid)
        if s is not None and s.status == SessionStatus.awaiting_human:
            # 停在断点时**全集群都没有活任务**：`_run` 已收尾，`_resume` 一旦起来状态就变
            # running 了（:226）。这条分支原先走下面的转发，而转发的结果是把会话钉死在
            # stopping——没有任何 worker 会有任务去取消它。就地落 stopped，并散会
            # （terminal=True：收 graphs/shell/editor，放弃这个断点就是放弃这场）。
            session = self.store.update(sid, status=SessionStatus.stopped, finished_at=_now())
            self._publish_status(session, "stopped by user")
            self._forget(sid, terminal=True)
            return True
        if self._ctl is not None and s is not None and s.status == SessionStatus.running:
            self.store.update(sid, status=SessionStatus.stopping)
            await self._ctl.publish("ch:ctl", json.dumps({"cmd": "stop", "sid": sid}))
            return True
        return False

    def enable_redis_control(self):
        """redis 模式 lifespan 调一次：订阅 ch:ctl，替别的 worker 取消它手上的任务。"""
        import redis.asyncio as aioredis
        from codeharness.configs.settings import settings

        self._ctl = aioredis.from_url(settings.redis.to_url(), decode_responses=True)

        async def _consume():
            pubsub = self._ctl.pubsub()
            try:
                await pubsub.subscribe("ch:ctl")
                async for msg in pubsub.listen():
                    if msg.get("type") != "message":
                        continue
                    try:
                        cmd = json.loads(msg["data"])
                    except Exception:
                        continue
                    if cmd.get("cmd") == "stop":
                        t = self.tasks.get(cmd.get("sid"))
                        if t and not t.done():
                            t.cancel()
            finally:
                try:
                    await pubsub.aclose()       # 重连前把这条专用连接还回去，否则每断一次漏一条
                except Exception:
                    pass                        # 连接已经断了：这条清理失败不许掩盖上面那个真原因

        async def _listen():
            """⚠ 这条分支**不许静默退出**（B5）。旧写法是 `except Exception: return`，注释的理由是
            「停机时连接被关是预期路径，不留孤儿任务栈」——它顺手把日志也留没了。后果是可查的：
            监听一旦因 Redis 抖动退出，跨 worker 的「停止」就无声失效，而 `stop()` 那半程**已经**把
            状态写成 stopping、PUBLISH 完返回 True ⇒ 会话**钉死在 stopping**、一行日志都没有，
            只能重启进程（旁边 :97-104 就是上一轮「把会话钉死在 stopping」的修复现场，同一条路上
            另一半还是黑的）。所以这里两点都补：异常留 warning，且**重连**而不是退出——
            这条路只该由进程退出（lifespan 的 `_ctl_task.cancel()`）结束。"""
            while True:
                try:
                    await _consume()
                    # listen() 正常结束 = 订阅流被关（不是取消）：同样是断线，落到下面重连
                    raise ConnectionError("ch:ctl 订阅流结束")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(f"ch:ctl 监听断了，2s 后重连（这期间跨 worker 的『停止』不可达）："
                                   f"{type(exc).__name__}: {exc}")
                    await asyncio.sleep(2)

        self._ctl_task = asyncio.get_running_loop().create_task(_listen())

    # ---- 插话（恢复被删除的 /chat 功能） ------------------------------------
    def enqueue_chat(self, sid: str, content: str, send_to: str = "") -> bool:
        chat = self.chats.get(sid)
        if not chat:
            return False
        chat.enqueue(content, send_to)
        return True

    # ---- 人工回答（interrupt → resume） -------------------------------------
    async def _ensure_graph(self, sid: str, *, register: bool = True):
        """取 (graph, config)。进程内字典命中是快路径；**未命中则按会话记录重建**——
        断点已落持久化 checkpointer，靠 thread_id 就能续上，不必是创建它的那个对象。

        `register=False` = **只读重建**（C11 回放面那两个 GET 走这条，B7）：不把重建结果挂进
        `graphs/projects/costs`，也不回写 `roles`。挂进去的代价不是内存，是**永久占用**——
        这三张表只有 `_forget` 清，而 `_forget` 只由 run/stop 调（断点态还刻意留着），
        于是「看一眼旧会话的回放」就把一个完整团队图钉在进程里一辈子；`_prepare` 里那句
        `store.update(roles=…)` 还会让只读路由顺手写库。只读那条路重建出来的图只用一次
        （`aget_state` / `aget_state_history`），用完即弃，下一次请求再重建。"""
        packed = self.graphs.get(sid)
        if packed:
            return packed

        session = self.store.get(sid)
        if not session:
            return None
        project = session.project_name or sid
        cm = self.costs.get(sid) or _seeded_ledger(session.cost or {})
        team, config, _init = await self._prepare(session, project, cm, persist_roles=register)
        # 重建出来的图只用于 resume / 只读回放：init 不能再喂一遍，否则等于重开一个线程
        if not register:
            return team, config
        self.projects[sid] = project
        self.costs.setdefault(sid, cm)
        self.graphs[sid] = (team, config)
        return self.graphs[sid]

    async def _thread_key(self, session, project: str) -> str:
        """这个会话在 checkpointer 里的唯一键（③，2026-09-26 用户拍板）。

        **为什么不能拿 `project_name` 当键**：它是用户输入的名字、同用户重名是合法的
        （`api/sessions.py` 的 409 只挡跨用户），而 `workspace/{name}` 同时是给人看的产物目录。
        拿它当身份 ⇒ 同用户两场**同名**会话共用一条 checkpointer 线程：B 的 messages/memories
        追加进 A 的线程，并发时两路写同一 thread。`sop/builder.py` 对模板线早就写着
        「server 多会话必须传会话唯一值，否则 checkpointer 按线程串台」——装配主路径没照做。

        **存量口径（老断点不作废）**：切换前落下的断点全在 `project_name` 名下。
        这里只在一个会话**确实跑过**（`started_at` 非空）且**新键下空**、**老键下有货**时回退用老键：
        老会话照旧续得上，而**新会话永远不会被带回老线程**——哪怕名字撞了，它没跑过 ⇒ 用 sid
        （少了 `started_at` 这一道，「同名新会话」会认领别人的线程，等于把这条修法反过来又踩一遍）。

        读不动 saver 时按新键走并留一声响：装配不该因为读不了断点而失败。判据四支在 `s21 t5`。
        """
        if not getattr(session, "started_at", ""):
            return session.id                      # 从没跑过 ⇒ 新会话，一律 sid
        legacy = project or ""
        try:
            saver = await self._saver()
            if await saver.aget_tuple({"configurable": {"thread_id": session.id}}) is not None:
                return session.id                  # 新键已有断点 ⇒ 继续用新键
            if legacy and await saver.aget_tuple({"configurable": {"thread_id": legacy}}) is not None:
                logger.info(f"会话 {session.id} 的断点在旧键 {legacy!r} 名下（切换前的存量），本次按旧键续跑")
                return legacy
        except Exception as exc:
            logger.warning(f"读 checkpointer 判断会话键失败，按新键 {session.id} 走："
                           f"{type(exc).__name__}: {exc}")
        return session.id

    async def _prepare(self, session, project: str, cost_manager, persist_roles: bool = True):
        """按会话三态装配三件套：sop=N7 模板线（9.3 扩展入口）；dynamic=S9.1 对照的 RoleZero 线；
        classic=默认经典线。resume 重建路径走同一函数——两张表（组队 × 路由）不会再各长各的
        （第十一处教训）。

        ⚠ `project` 只用来**算会话键的存量回退**（`_thread_key`）与产物目录语义，**不再**直接当
        `thread_id`（③）：多会话共用同名项目不能在 checkpointer 里串台。

        `persist_roles=False`（只读回放，B7）：一列都不回写。装配出口那三个赋值原先无条件做，
        而 `store.update(roles=…)` 是**写库**——只读路由顺手写库正是 B7 那条的另一半。"""
        from codeharness.const import RequirementTag
        from codeharness.environment.team_graph import SOP
        from codeharness.team import prepare_project, _make_llm, classic_team
        thread_key = await self._thread_key(session, project)   # 装配前先定身份，两条装配路共用
        # 一次装配只建一个网关：会话的 llm_override 就在这里落地。经典线原先走
        # prepare_project 的 agents=None 兜底，而 _default_agents 会另建一个不认
        # override 的网关——所以三条线都显式组队。
        llm = _make_llm(cost_manager, getattr(session, "llm_override", None))
        if getattr(session, "sop", ""):
            from codeharness.sop.builder import build_team_from_template, get_template
            edges = get_template(session.sop).edges
            team, config, init = build_team_from_template(session.sop, llm,
                                                          checkpointer=await self._saver(),
                                                          idea=session.idea, thread_id=thread_key)
            names = sorted({str(r) for roles in edges.values() for r in roles})
            entry = str(next(iter(edges.get(RequirementTag.USER_REQUIREMENT) or []), ""))
        else:
            sop = None
            paradigm = getattr(session, "paradigm", "classic")
            if paradigm == "dynamic":
                from codeharness.team import dynamic_assembly
                agents, sop = dynamic_assembly(llm)
                # C1-③ 现场招的成员在**建图之前**并进装配：LangGraph 的节点集 compile 时就定死，
                # 所以生效点是「下一次起跑/续跑」，不是运行中热插（API 侧同判：只有 dynamic 线能招人，
                # classic/react 线没人会点名新节点，招进来就是个醒不过来的死节点）。
                for d in getattr(session, "role_defs", None) or []:
                    from codeharness.team import build_hired_role
                    agents[d["name"]] = build_hired_role(d, llm)
                from codeharness.team import sync_roster
                sync_roster(agents)           # 队长的 {team_info} 得看见新成员，否则点名点不到
            elif paradigm == "react":                     # 9.2 策略曲线第三腿：经典队形×REACT 循环
                from codeharness.team import react_assembly
                agents = react_assembly(llm)
            else:
                agents = classic_team(llm)
            team, config, init = prepare_project(session.idea, thread_key, agents=agents,
                                                 checkpointer=await self._saver(),
                                                 cost_manager=cost_manager, sop=sop)
            names = sorted(agents)
            # 插话默认目标 = 真正订阅 USER_REQUIREMENT 的那个节点（dynamic 用自己的表，
            # classic/react 走 prepare_project 里的 team_graph.SOP）
            route = sop or SOP
            entry = next((str(r) for r in (route.get(RequirementTag.USER_REQUIREMENT) or [])
                          if r in agents), "")
        # 装配出口回填：前端直聊下拉与 /chat 的目标校验都读这两个值。原先前端硬编码
        # ProductManager/Engineer2/DataAnalyst——一个都不在装配里，追问会被 route 静默丢掉。
        # ⚠ 只读回放（B7）连这一列也不写：`store.update` 是**写库**，而这两个 GET 不该有写。
        if persist_roles:
            if names != list(session.roles) or entry != session.entry_role:
                self.store.update(session.id, roles=names, entry_role=entry)
            session.roles, session.entry_role = names, entry
        # C190：装配那份名单顺手缓存成集合——`_role_signal` 每条 `on_chain_*` 都要问「这是不是一个
        # 角色节点本体」，而 `store.get(sid)` 在 Redis 档是一次读库。`_prepare` 的两条调用路
        # （`_run` 起跑、`_resume` 续跑）都经过这里；只读回放（B7）也会经过，缓存无害。
        self._node_names[session.id] = set(names)
        # C192（10-10 真模型活体现证）：**队必须在这里就存在**，否则这句拿 None、默认目标留在
        # 构造函数的 `TEAMLEADER_NAME="Mike"`。起跑那趟不会缺（`_run` 先建队再 `_prepare`），
        # `_resume` 却是先 `_ensure_graph`（走到这里）后才 `self.chats.get(sid) or self._make_chat(sid)`
        # 建队 ⇒ **停过一次待批、
        # 再续跑**的每一场，空目标插话都被 route 当成不存在的角色丢掉（真模型那场日志逐字：
        # `插话指名投给不存在的角色 'Mike'，该条已丢弃（在册：['Architect', 'Engineer', 'PM',
        # 'PMManager', 'QA']）`——而 `entry_role` 明明是 PM）。只读回放（B7）不建队：那两条 GET
        # 不该留下任何写侧痕迹，同上面 `persist_roles` 那一列的判法。
        chat = self.chats.get(session.id)
        if chat is None and persist_roles:
            chat = self.chats[session.id] = self._make_chat(session.id)
        if chat is not None and entry:
            chat.default_target = entry                 # 空目标也要落在真节点上
        # N9：全链路 trace 的唯一注入点（_run 与 _resume 都从这里拿 config）。
        # 节点内裸 model.ainvoke()/tool.ainvoke() 靠 langchain-core 的 var_child_runnable_config
        # 继承，网关与节点里**不得**再传一次——同一 handler 既显式又继承会双 span。
        from codeharness.observability import callbacks
        config["callbacks"] = callbacks()
        return team, config, init

    async def _ckpt_page(self, graph, config: dict, limit: int, before: dict | None = None) -> dict:
        """取一页超步摘要（新→旧），多取一条探 `has_more`——翻页口径与 B2 的事件回放同判。

        ⚠ `before` 必须走 **关键字参数**（开区间上界）。把 `checkpoint_id` 塞进 config 是另一个
        语义：「从那个点往回看，**含它自己**」（闭区间），翻页会重复吐同一条——B2 那轮踩过同族形状。
        这一层刻意**不带 values**：`aget_state_history` 会把每份 state 反序列化出来
        （langgraph 的接口就这样），但只在这一格内存里活着，出页即丢。真数据层实测最单个
        thread 有 711 份、单份最大 62 KB，整份跟着列表回给浏览器等于一次请求搬几十 MB。
        `writes` 这一格 09-25 删掉了，别当「实测为空」：本机 langgraph 的 `snap.metadata` 只有
        `parents / source / step` 三个键（两节点小图现证，见 `plan/team-runtime.md` C11 行末），
        旧写法 `sorted(writes) if isinstance(writes, dict) else []` 于是**永远**给前端一个空数组——
        那是个装死的字段，而界面还写了「空则退回 tasks」的兜底，看着像有两条数据源。
        这一步里谁干活由 `tasks` 给，那才是真值。"""
        out: list[dict] = []
        async for snap in graph.aget_state_history(config, before=before, limit=limit + 1):
            md = snap.metadata or {}
            conf = (snap.config or {}).get("configurable") or {}
            out.append({"checkpoint_id": conf.get("checkpoint_id") or "",
                        "step": md.get("step"), "source": md.get("source"),
                        "ts": str(snap.created_at or ""), "next": list(snap.next or ()),
                        "tasks": [getattr(t, "name", "") for t in (snap.tasks or ())]})
        has_more = len(out) > limit
        page = out[:limit]
        return {"checkpoints": page, "has_more": has_more,
                "next_before": page[-1]["checkpoint_id"] if (page and has_more) else ""}

    def answer_human(self, sid: str, content: str) -> bool:
        s = self.store.get(sid)
        if not s:
            return False
        if self.is_running(sid):
            # _run 与 _resume 共用同一个 tasks[sid] 槽：抢槽会让先结束的一方 pop 掉另一方的
            # 引用（is_running 误报 False、stop() 取消错对象、跨 worker 停止失配）。
            # 判据用活任务视角而不是 store.status：实测 interrupt 收尾时 _run 会把
            # awaiting_human 覆写成 finished（tests/s17::t1 读数），状态在这儿不可信。
            return False
        if s.status == SessionStatus.running:
            # C131（10-02 复审批）：本 worker 没有活任务、store 却说 running ⇒ 场跑在**别的**
            # worker 上（多 worker 部署）。改前放行 → 落错 worker 的回答会对同一 thread 发起
            # 第二个 `astream_events(Command(resume))`，与持任务那个 worker 并发写同一 checkpoint
            # ＝双跑同一超步、双倍发费。照 stop() 的转发分支同一条「running 且本 worker 无任务」
            # 判定拒收（转发要给 ch:ctl 加 answer 命令，另立不混本件）。单进程不可达：
            # tasks 有活 ⇒ is_running 已拦；无活 ⇒ 收尾时状态必已离开 running。
            return False
        self._spawn(sid, self._resume(sid, content))
        return True

    @staticmethod
    async def _pending(graph, config) -> bool:
        """该 thread 是否停在待恢复处（interrupt 未答）。

        ⚠ 必须用 `aget_state`：异步 saver 不支持同步 `get_state`，一调就抛
        NotImplementedError。取不到状态时按"是"处理——让 resume 自己决定，
        比误拒用户回答要好。"""
        try:
            state = await graph.aget_state(config)
        except Exception:
            return True
        nxt = getattr(state, "next", None)
        return bool(nxt) if nxt is not None else True

    async def _resume(self, sid, content):
        """必须重装同一套 ContextVar：create_task 复制的是 HTTP 请求的 context，
        不重装则内核 report.py 拿不到 sink，人工回答之后的产物块会被静默丢弃。"""
        packed = await self._ensure_graph(sid)
        if not packed:
            return
        graph, config = packed
        if not await self._pending(graph, config):
            return                                   # 没停在待恢复点，别把会话误标成 finished
        # C62：恢复值按 **interrupt id 定向投递**（`Command(resume={id: content})`）。
        # 标量 resume 在同一次恢复运行里会被后续 interrupt **冒名领走**——langgraph 的
        # `get_null_resume` 会沿 parent scratchpad 回退，子图里排在后面的节点（`_ask`）
        # 拿到的「回答」就是前一个 interrupt 的恢复值。实测（真图 + FakeLLM，探针
        # E:/tmp/ch_c59b_probe.py）：readonly 会话的命令列表里先出现需批的 write_file、
        # 后出现 ask_human，批完卡之后 `_ask` 的 `interrupt()` **直接返回卡 id**、
        # 问题被静默跳过，用户永远看不到提问。按 id 投递后值只进那个确切的 interrupt，
        # 后续 interrupt 照常停车。dict 的键必须是 interrupt 的 `id`（xxh3_128 hexdigest，
        # `_loop.py:910` 按这个形状识别 map 形式；`Interrupt.id` 的生成正是同一摘要）。
        payload = content
        try:
            state = await graph.aget_state(config)
            for t in getattr(state, "tasks", ()) or ():
                for it in getattr(t, "interrupts", ()) or ():
                    iid = getattr(it, "id", None)
                    if iid:
                        payload = {iid: content}
                        break
                if payload is not content:
                    break
        except Exception:
            payload = content                        # 取不到状态时按标量恢复（_pending 同一口径）
        chat = self.chats.get(sid) or self._make_chat(sid)
        self.chats[sid] = chat
        self.store.update(sid, status=SessionStatus.running)
        try:
            with self._session_ctx(sid):
                async for ev in graph.astream_events(Command(resume=payload), config, version="v2"):
                    self._translate(sid, ev)
            await self._settle(sid)
        except asyncio.CancelledError:
            session = self.store.update(sid, status=SessionStatus.stopped, finished_at=_now())
            self._publish_status(session, "stopped by user")
            self._forget(sid, terminal=True)
            raise
        except Exception as exc:
            self._fail(sid, exc)
        finally:
            self.tasks.pop(sid, None)

    # ---- checkpointer（进程级共用一个文件型 saver） --------------------------
    async def _saver(self):
        """断点必须落盘：内存型 saver 一重启就丢，interrupt 的会话再也续不上。

        不可用时退回内存型并照常跑通单进程流程——存储后端故障不该让平台起不来。
        必须是协程：工厂内部要拿运行中的事件循环来建 AsyncSqliteSaver。"""
        if self._ck is None:
            from codeharness.environment.checkpoint import default_checkpoint_path, make_checkpointer
            from server.settings import WORKSPACE_ROOT
            self._ck = await make_checkpointer(default_checkpoint_path(WORKSPACE_ROOT))
        return self._ck

    # ---- 报道槽 → 总线（内核块的唯一通道） ----------------------------------

    @staticmethod
    def _owner(md) -> str:
        """落点身份＝**外层节点名**（动态线里就是角色名）。两侧共用这一个来源，不靠名字相等去赌：
        事件侧读 `metadata["checkpoint_ns"]` 首段，报道槽侧读节点内 `get_config()` 的同一个键——
        `tests/manual_c174_llm_identity.py` 现证两者逐字相同（`Alice:<uuid>` vs `Alice:<uuid>|think:<uuid>`）。
        不在图里（单测直接喂 sink、合成事件不带 ns）退空串：事件侧同形，替身场的落点照旧对得上。"""
        return str((md or {}).get("checkpoint_ns") or "").split(":", 1)[0]

    def _config_owner(self) -> str:
        """报道槽那一侧的 owner（此刻正跑在某个节点里，config 在 contextvar 上）。"""
        try:
            from langgraph.config import get_config
            cfg = get_config() or {}
        except Exception:                      # 不在图里：单测直接调 sink 就是这一支
            return ""
        return self._owner(cfg.get("configurable"))
    def _make_sink(self, sid: str):
        bus = self.bus

        def sink(event: dict):                    # 普通函数（桥接铁律）
            # P2：内核事件（非块，带 kind 键——`report.emit_event`）直接上流：不进块逻辑
            # （不登记落点、不摘槽）；kind 从载荷走，前端对未知 kind 静默忽略且游标照推。
            if event.get("kind"):
                bus.publish(sid, **event)
                return
            # 记一块「现在能收逐片正文的」：内核块在 `async with` 里先开（meta 走到这儿），
            # 它里面那一笔 LLM 调用的散文就投进它；收口即撤。上一版的「整段重发去重」
            # （比一下内核定稿是不是已逐片发过的那句话、是就丢掉）到此作废——逐片走 `live`
            # 通道、定稿走 `content`，两份内容在结构上就不会同屏，不必再靠字符串比对去猜。
            uid, nm = event.get("uuid"), event.get("name")
            # C104 之一（计划卡的频道身份）：`Plan._report_plan` 每推进一次就发一颗**全新 uuid** 的 Task
            # `object`，而载荷本来就是整份计划 ⇒ 前端按 uuid 聚块，一场跑下来攒出 N 张几乎一样的计划卡。
            # 这一路的身份按 (块, 角色) 收敛成一颗：后一次覆盖前一次，角色之间仍各一张卡。
            # C175（10-05）把收敛从「只改 object」扩到**整块**——改前 `task_block` 自己那颗 uuid
            # （meta/live/content/end_marker）仍在，于是一次计划更新在界面上出**两张**「更新任务清单」
            # （一张念正文、一张念对勾）。扩完之后：同一颗卡先念逐片、清单到达后念对勾。
            if event.get("block") == "Task":   # C175：整块都收敛（改前只改 object ⇒ 双卡）
                event["uuid"] = uid = "plan-" + (event.get("role") or "")
            if uid:
                if nm == "end_marker":
                    # 按 uid 摘，不再问「槽里那个是不是它」：改前并发两块时后开的把先开的顶出单槽，
                    # 先开那块收口时槽里已经不是它 ⇒ 槽永久留着别人的块（C174）
                    for k in [k for k, v in self._live_blk.items() if k[0] == sid and v[0] == uid]:
                        self._live_blk.pop(k, None)
                elif event.get("block") in LIVE_BLOCKS and nm == "meta":
                    # 第三格是这块声明的正文字段名单（来自开块那条 meta）：逐片抽散文时按它门控。
                    v = event.get("value")
                    # C104 之二（守卫）：落点表的**准入**只认开块那条 meta。改前任何 `block ∈ LIVE_BLOCKS` 的事件
                    # 都登记，于是两条真形状一起出事：
                    #   a) 计划卡那颗随机 uuid 的 object 把落点抢走 ⇒ 逐片投进一块渲染不出 live 的容器、
                    #      静默期兜底行被 `if not self._live_blk` 抑制（本文件 :962 那格）、**名单也丢**
                    #      （退回按长度挑＝第六件治过的「键名上屏 + mermaid 源码上屏」复发）；
                    #   b) 交错块里上一块的定稿（同块名、不同 uid）把落点从当前开着的块抢回去。
                    # 「已登记那块自己的后继事件也重新登记」那一支删掉了：它写回的就是字典里已有的那个三元组，
                    # 观察上恒等——变异刀 n2（只认 meta）存活就是这条等价性的证明。等价就删，别留第二段看似有条件的代码。
                    self._live_blk[(sid, self._config_owner())] = (
                        uid, event.get("block"),
                        v.get("prose_fields") if isinstance(v, dict) else None)
            bus.publish(sid, kind="report", **event)

        return sink

    @contextmanager
    def _session_ctx(self, sid: str):
        """装/卸七个 ContextVar。token 只能是局部变量——挂在 self 上会被并发会话互相覆盖，
        随后 reset 到别人 context 里创建的 token 直接 ValueError。

        批次36 多装两个：PERMISSION（工具审批的会话级免审档）与 APPROVAL_IO（待批通道，
        内核 gate 只认它的 sid/decision/request 三个口，因此内核保持零 server / 零 redis 依赖）。

        N9：同时套一层 Langfuse 的会话属性（session_id/user/tags）——两个 astream 循环共用的
        唯一上下文口，OTel 上下文按 asyncio task 隔离，并发会话不串；未开启时是 nullcontext。"""
        from contextlib import ExitStack
        from codeharness.provider.context_budget import ContextBudget
        from codeharness.runtime import (CURRENT_PROJECT, CURRENT_SESSION, REPORT_SINK, CHAT_SINK,
                                         CURRENT_USER, APPROVAL_IO, PERMISSION, CURRENT_BUDGET)
        from codeharness.observability import session_attributes
        from platforms.approval_store import ledger_for
        session = self.store.get(sid)
        pairs = (
            (SESSION_ID, SESSION_ID.set(sid)),
            (CURRENT_PROJECT, CURRENT_PROJECT.set(self.projects.get(sid, sid))),
            # ③：**会话身份**单独一个 var（= sid）。CURRENT_PROJECT 是产物目录名、可重名，
            # 只用于路径与切片作用域；身份（thread_id、常驻 shell / Editor 的登记键）认这一个。
            (CURRENT_SESSION, CURRENT_SESSION.set(sid)),
            (REPORT_SINK, REPORT_SINK.set(self._make_sink(sid))),
            (CHAT_SINK, CHAT_SINK.set(self.chats.get(sid))),
            # N1：user_id 贯穿进内核（记忆/经验池的切片键从这里兜底），auth 关恒 "default"
            (CURRENT_USER, CURRENT_USER.set(getattr(session, "user_id", "default")
                                            if session else "default")),
            # 取不到会话时按最严的 readonly（多问一次，不是放行一切）
            (PERMISSION, PERMISSION.set(getattr(session, "permission", "") or "readonly")),
            # C87：走 `ledger_for` 唯一出口——原先这里无条件建 Redis 版，而 app.py 在 Redis 不可达时
            # 只把 store/bus 退回进程内，台账没退 ⇒ 只读档第一个写工具就在 gate 上 ConnectionError，
            # 且与 approvals 路由那条读路对不上（见 approval_store.py 的注释）。
            (APPROVAL_IO, APPROVAL_IO.set(ledger_for(sid))),
            # P1：会话预算尺——工具层（clip 的 16 处额度换算）够不到 llm，经这条读会话校准系数 k。
            # 账本必已建（`_run` 先建 / `_ensure_graph` setdefault 播种），取不到也只是 k=1 恒等。
            (CURRENT_BUDGET, CURRENT_BUDGET.set(ContextBudget.of(meter=self.costs.get(sid)))),
        )
        with ExitStack() as stack:
            stack.enter_context(session_attributes(self.store.get(sid), self.projects.get(sid, sid)))
            try:
                yield
            finally:
                for var, tok in reversed(pairs):
                    var.reset(tok)

    def _retire_ring(self, sid: str):
        """C115：散会（terminal）才把这场的进程内 ring 落冷档、卸出内存。

        为什么挂在这一个出口：五处终态（`_settle` 的正常收口、`_fail`、两处取消、两条 stop 路）
        本来就全收进 `_forget(terminal=True)`——按形状找齐比逐处补更短，也不会漏第五处。
        停在待人工处（`terminal=False`）走不到这里；真走到了也不会误卸，守卫在 `bus.retire()` 里。

        这是**旁路，不许打断主流程**（同「副作用失败只记日志+空操作」那条口径）：Redis 版 bus 没有
        `retire`（它那本账在 Redis，不是这里的病）⇒ `getattr` 拿到 None 就什么也不做；落盘失败也只记
        一行 warning，**ring 原样留着**——宁可占着内存，不丢历史（无 Redis 档 ring 是唯一历史源，
        见 `platform-infra.md` §1.12 的 D 臂读数）。写盘排进 `to_thread`：满窗口 15000 条 ≈ 3 MB，
        别让它卡住事件循环上别人的流。
        """
        retire = getattr(self.bus, "retire", None)
        if retire is None:
            return

        async def _run():
            try:
                await asyncio.to_thread(retire, sid)
            except Exception as exc:
                logger.warning(f"[ring-retire] {sid} 落冷档失败，ring 原样留着：{type(exc).__name__}: {exc}")

        try:
            # 任务句柄存进 `_closers`（现成的「在途收尾任务别被 GC 收走」登记处）：asyncio 的
            # fire-and-forget 任务如果没人持引用，可能在跑之前就随协程被回收——那这场的 ring 就白留了。
            task = asyncio.get_running_loop().create_task(_run())
            self._closers.add(task)
            task.add_done_callback(self._closers.discard)
        except RuntimeError:                              # 线程侧收口（没有 running loop）：就地写
            try:
                retire(sid)
            except Exception as exc:
                logger.warning(f"[ring-retire] {sid} 落冷档失败，ring 原样留着：{type(exc).__name__}: {exc}")

    def _end_stream_rows(self, sid: str, only: str = ""):
        """给打字机兜底行收口（C172）：先补一条换行分隔（只在这行**真有过逐片**时补），再发 end_marker。

        两个调用点：`on_chat_model_end` 的正常收口（`only` 给那一颗），以及 `_forget` 的散会清扫
        （不给 `only`，把这一场还挂着的全收掉）——取消/异常那两条路走不到前一个调用点。
        分隔符是给 C173 的：兜底行的 uuid 按节点名复用，第二笔块外调用重开**同一行**，而换行分隔
        只在同一次 `_ProseStream` 内部给（`emitted` 每笔调用新建），前端又是 `live.join('')`
        ⇒ 现证过接缝零分隔，两笔的话粘成一句。
        「没字不补」这条守卫不是客气：`ChatNode.vue:14` 的判据是 `open || text`，一个光秃秃的
        `\\n` 会让「收口后零字的 Think 行不渲染」那条规则失效——屏上多一行空白 Think。
        """
        rows = self._stream_rows.get(sid) or {}
        for uid in ([only] if only else list(rows)):
            had = rows.pop(uid, False)
            node = uid[len("stream-"):] if uid.startswith("stream-") else uid
            if had:
                self.bus.publish(sid, kind="report", block="Thought", uuid=uid,
                                 name="live", value="\n", role=node)
            self.bus.publish(sid, kind="report", block="Thought", uuid=uid,
                             name="end_marker", value=None, role=node)
        if not rows:
            self._stream_rows.pop(sid, None)

    def _forget(self, sid: str, terminal: bool):
        """散会（`terminal=True`）才清图与整场的进程态；**停在待人工处（`terminal=False`）只扫在途表**。

        B1：`costs` 从前无条件清，而 `_park` 的约定恰恰是「图还活着、断点已落 checkpointer、等 resume」
        ——图里那个 gateway 仍持着 `_prepare` 建的那**同一个** CostManager 继续累计。把它从进程字典里
        抹掉之后 runner 就再也读不到它，下游三处全哑：`_sync_cost` 与 `_trace_span` 拿到 `cm is None`
        静默 return，`_publish_status` 发的是 `"cost": {}`，而前端 `if (v.cost)` 里 `{}` 是**真值**
        ⇒ 顶栏金额被覆盖成 0，刷新又跳回批准前那个数；`_publish_max_tokens` 读同一本账，B8 那条截断
        提示一起丢。最硬的不对称就在隔壁：`_resume` 早就为被丢掉的 `chats` 写了 `or self._make_chat(sid)`
        兜底，同一个坑只填了一边——而账本没有「重建兜底」这种补法（重建出来的是第二本，图里那本是活的）。
        `_last_span`/`_trunc_reported` 同理：它们是**这一跑**的增量基线，清了会让 resume 后的 trace
        增量从 0 起算（虚高一笔）、截断提示重报一次。

        注意这不是「永不回收」：`_settle` 的正常收口、`_fail`、取消、以及 stop 的那两条路都走
        `terminal=True`，断点态只是**留到这一场真正结束**为止。"""
        self.chats.pop(sid, None)
        # C172：散会前把还挂着的兜底行收掉。**顺序要紧**：`_retire_ring` 就在下面这个 `if terminal:`
        # 里把这场的 ring 落冷档，补在它之后等于冷档里那条一直开着——回放同形，刷新也不会好。
        self._end_stream_rows(sid)
        # C190：与上面同族的一半——**取消 / 异常走不到 `on_chain_end`**，还开着的角色车道在这儿收。
        # 不收就是那个角色在界面上永远「在跑」（前端只认 `b.closed`，冷档回放同形，刷新也不会好）。
        # 顺序同 `_end_stream_rows` 的理由：`_retire_ring` 就在下面 `if terminal:` 里落冷档，
        # 补在它之后等于冷档里那条车道一直开着。
        self._close_lanes(sid)
        for k in [k for k in self._lane_t0 if k[0] == sid]:
            self._lane_t0.pop(k, None)
        self._lane_open.pop(sid, None)
        if terminal:
            self.costs.pop(sid, None)
            self._last_span.pop(sid, None)
            self._trunc_reported.pop(sid, None)
            self._node_names.pop(sid, None)      # C190：装配名单与车道序号也是 per-session 的，
            self._lane_seq.pop(sid, None)        # 不跟着 terminal 收就是「只增不减」那张老账
            self._retire_ring(sid)
        # 中断的调用不会走到 on_chat_model_end，在途表必须在这里扫干净，否则永久留着
        for k in [k for k in self._call_t0 if k[0] == sid]:
            self._call_t0.pop(k, None)
        for k in [k for k in self._prose if k[0] == sid]:
            self._prose.pop(k, None)
        for k in [k for k in self._prompts if k[0] == sid]:
            self._prompts.pop(k, None)
        for k in [k for k in self._used_kernel_block if k[0] == sid]:
            self._used_kernel_block.pop(k, None)
        for k in [k for k in self._silent_runs if k[0] == sid]:       # C177：同规扫（停在待人工处也要扫干净）
            self._silent_runs.pop(k, None)
        for k in [k for k in self._blk_of if k[0] == sid]:      # C130：快照表同规扫
            self._blk_of.pop(k, None)
        for k in [k for k in self._live_blk if k[0] == sid]:   # C174：键是 (sid, owner)，按前缀摘
            self._live_blk.pop(k, None)
        if terminal:
            self.graphs.pop(sid, None)
            self.projects.pop(sid, None)
            # 常驻 shell / Editor 按**会话 id** 收（③）：不收就是每会话漏一个 cmd.exe；
            # 而按项目目录名收会把**同名另一场**的壳顺手关掉（那两个注销表也改成按会话 id 索引了）。
            from codeharness.tools.libs.terminal import close_terminal
            task = asyncio.create_task(close_terminal(sid))
            self._closers.add(task)
            task.add_done_callback(self._closers.discard)
            # Editor 视图态（current_file/行窗）与会话同生命周期——B3 命令面登记后必收，
            # 否则字典按会话只增不减
            from codeharness.tools.libs.editor_tools import close_editor
            close_editor(sid)

    async def _interrupt_payload(self, sid: str):
        """活图视角问「这个 thread 是不是停在 interrupt 上」，是则回它的 payload（ask_human 的问题或待批项）。

        为什么必须问活图：`astream_events(v2)` 在 langgraph 1.2.11 下**不发** interrupt 事件
        （实测事件名只有 `on_chain_start/stream/end`）。旧代码把落态押在 `_translate` 的
        `on_interrupt` 分支上，那条分支永不触发 → 停在待批处的会话被写成 `finished`、
        `graphs` 被 pop、审批卡也进不了活流（前端只在切会话时 GET 一次，用户看不到新卡）。
        A4 真模型那批（PLAN §2）取到的读数就是这条。graphs 已被清掉时按「没停」走 finished——
        那种情况线程里没有待恢复的断点，误报 awaiting_human 反而会让 start/stop 卡住。"""
        packed = self.graphs.get(sid)
        if not packed:
            return None
        graph, config = packed
        try:
            state = await graph.aget_state(config)
        except Exception:
            return None
        for task in getattr(state, "tasks", None) or ():
            for intr in getattr(task, "interrupts", None) or ():
                return intr.value
        return None

    def _park(self, sid: str, payload):
        """停在待人工处的统一落态：`awaiting_human` + 把卡推给活流 + graph 留着供 resume。"""
        self.store.update(sid, status=SessionStatus.awaiting_human)
        item = payload.get("approval") if isinstance(payload, dict) else None
        if item:
            # 待批项是内核 gate 用 HSETNX 登记过的那条，这里只推给界面，不重复登记
            self.bus.publish(sid, kind="approval", name="requested", value=item)
        else:
            question = payload.get("question", "") if isinstance(payload, dict) else str(payload)
            self.bus.publish(sid, kind="ask_human", value=question)
        self._publish_status(self.store.get(sid), "awaiting human input")
        self._forget(sid, terminal=False)

    async def _settle(self, sid: str):
        """事件流收口时的落态——**停在待人工处就不许写 finished**。

        旧写法（A 项那次）读 `store.status == awaiting_human` 来判，而那个字段本来就是本函数要写的东西：
        真正置它的那条路（`on_interrupt`）在生产里从不触发，于是判据恒假。现在改问活图。
        两条 teardown（`_run`/`_resume`）共用本出口，不再各写一遍。"""
        payload = await self._interrupt_payload(sid)
        if payload is not None:
            self._park(sid, payload)
            return
        session = self.store.update(sid, status=SessionStatus.finished, finished_at=_now())
        self._publish_max_tokens(sid)
        await self._publish_plan_open(sid)
        self._publish_status(session, "run completed")
        self._forget(sid, terminal=True)

    def _publish_max_tokens(self, sid: str):
        """B8：这一跑里有几步被输出上限截断——有就发一条 `turn/end`，形状照参照系
        （`conversation-nodes/turn-max-tokens.ts:42` 读的是 `turn/end` 的 `reason.kind==='max-tokens'`）。

        两个口径差写清楚，别当成「和参照系一模一样」：
        ① 位置：参照系也是把提示锚在轮尾（closing Assistant 与 turn-tail 之间），不是贴在截断那一步；
        ② 计数：它是「这一轮至少有一步撞了上限」的聚合，我们这里是一跑收口说一次——resume 过的长跑
           靠 `_trunc_reported` 记「上次报到第几笔」，新增的截断才会再冒一条，不会三跑刷三条。

        为什么不在 `_translate` 的 `on_chat_model_end` 上顺手发：那条钩子在**最伤的那种截断**上收不到信号
        （JSON 被切半→解析失败→走 repair，`data.output` 里没有 finish_reason；活体 classic 线三次
        length 收尾，零条提示）。每笔调用落账时都带着自己的 response_metadata，记账口是唯一不漏的形状。
        空 content 的 length 收尾走的是另一条已可见的路：`gateway.structured` 抛 ValueError → `_fail`
        发 error 行，文案自己写着「模型输出被 max_token=… 截断」。"""
        cm = self.costs.get(sid)
        n = getattr(cm, "truncated_calls", 0) or 0
        if n > self._trunc_reported.get(sid, 0):
            self._trunc_reported[sid] = n
            self.bus.publish(sid, kind="turn", name="end", value={"reason": {"kind": "max-tokens"}})

    async def _publish_plan_open(self, sid: str):
        """C148：收口时把「Plan 还剩几条没做完」发进事件流。

        为什么值得发：`_settle` 落 `finished` 之前只回答过一件事——「有没有停在待人工」。它不回答
        「事办没办完」，而**完成度本来就在账上**（`TeamState.plans` 每角色一份 Plan 状态机，对勾=
        `Task.is_finished`，`role_zero._plan_status` 早就拿它画清单给模型自己看）。所以这一件**纯读这本账**，
        不新增判据、不据此再跑一轮（那属循环语义，要另拍）。

        形状端到端照隔壁 `_publish_max_tokens`：事件 `turn/end` + `reason.kind`，锚在轮尾。
        计数走事件 value（前端放进 `Block.meta`，不扩 Block 字段）。
        graphs 已被清掉（散场、或这落在别的 worker）就当不知道——**宁可不发，不编一个 0 上去**。
        """
        packed = self.graphs.get(sid)
        if not packed:
            return
        graph, config = packed
        try:
            state = await graph.aget_state(config)
        except Exception:
            return
        open_n = total = 0
        for dump in ((getattr(state, "values", None) or {}).get("plans") or {}).values():
            for t in (dump or {}).get("tasks") or []:
                total += 1
                if not t.get("is_finished"):
                    open_n += 1
        if open_n:
            self.bus.publish(sid, kind="turn", name="end",
                             value={"reason": {"kind": "plan-unfinished",
                                               "open": open_n, "total": total}})

    @staticmethod
    def _fail_kind(exc: Exception) -> tuple[str, str]:
        """C116：把「一场为什么死」分成三族——`blocked`（供应侧把内容拦了）、`context_overflow`（我们把
        上下文撑爆了）、`crash`（我们自己的错）。C147 补的是第三族那格。

        为什么值得分（现场现证，10-01 22:46）：StepFun 对某一发回 `451 censorship_blocked` 时，`_fail` 走的
        是与编排崩溃**完全同一条路**——状态 `failed`、`error` 是异常 repr、日志只有 `[session-failed]`。
        于是运维 grep 不出「外部拦停」与「代码炸了」，用户看到的是一句
        `APIStatusError: Error code: 451 - {'error': {'message': 'The content you provided...'}}`，
        不知道该改提示词还是该报 bug。

        **只做分类，不做续跑**：被拦那一发没有可用产出，要让图继续就得先定「节点无输出怎么路由」，
        那是另一件设计件（见 `platform-infra.md` §1.13 的档位 ②）。
        这里也**不碰重试判据**（那是 `codeharness/provider/gateway.py::_retryable` 的事，且在审查线地界，
        不跨面、不把两处口径合并成一颗共享函数）。
        """
        # C154：拦停的判别收成 `gateway.blocked_reason` 一处定义——`_think` 的降级（档位②）要认同一个
        # 形状，规则的第二个读者出现时规则就该搬进被读的那层。死因**文案**留在本函数（s17 t9④ 按它
        # grep，逐字不变），这里只借「认出拦停」与那句前缀。
        from codeharness.provider.gateway import blocked_reason
        reason = blocked_reason(exc)
        if reason:
            return "blocked", (reason + "：这一发没有可用产出，本场就此停在这一点；"
                               "此前已产出的文件与会话记录都保留。要接着做，请改写触发拦停的那段内容后重开一场。")
        code = getattr(exc, "status_code", None)
        body = f"{getattr(exc, 'body', '') or ''}{exc}"
        # C147：超窗单列一族。它和编排崩溃在改前是同一条路（`_retryable` 按 `gateway.py:494` 的口径
        # 故意不重发 400 ⇒ 直冒到这里记 `kind=crash`），后果是运维 grep 不出「我们把话撑爆了」与
        # 「代码炸了」，用户拿到一句裸 repr，不知道该切文件还是该报 bug。
        # ⚠ 这四条形状**没在真端点上现证过**（要撞一次窗才拿得到，本仓在这台模型上没撞过）：前两条是
        #   OpenAI 兼容口的口径，第三条是 DashScope 系（`Range of input length should be [1, N]`），
        #   第四条兜「context window」这种改写。真撞窗的那发原文一到，按 C116 的做法把它补进名单。
        low = body.lower()
        if code == 400 and ("maximum context length" in low or "context_length_exceeded" in low
                            or "range of input length" in low or "context window" in low):
            return "context_overflow", ("上下文超出了模型窗口（HTTP 400 一类）：这一发没被受理，"
                                        "此前已产出的文件与会话记录都保留。要接着做，把长输入分段（一次少读几个文件、"
                                        "或让工具少带回些正文），或在 `.env` 开 `LLM__CONTEXT_LENGTH` 让网关按预算裁。")
        return "crash", ""

    def _fail(self, sid: str, exc: Exception):
        # C117：会话行在起跑之后被人删掉 ⇒ 这场不是「跑失败了」，是「行没了、按散会收」。
        # 改前这里先打 `[session-failed]`（运维按这一行计数），再自己撞第二次 KeyError 被咽喉
        # `guarded` 咽掉，于是同一场留两条互相矛盾的告警——现证 `E:/tmp/ch_c116_s17.out:447`：
        # `session-failed sid=41ecad33 … KeyError: '41ecad33'` 在前、`runner-dropped sid=41ecad33` 紧跟。
        # 也不发 error 事件、不 `_publish_status`：给一个已删的会话发事件，会把它那支 ring 重新建出来
        # （正撞 C115 刚收的账——驱逐的前提是没人再往里投）。
        if self.store.get(sid) is None:
            # ⚠ 这条文案里**不许出现** `[session-failed]` 字面量：本仓判据就是按那串 grep 的，
            #   写了就会让「不打假告警」这件事自己造出一条假阳性（10-02 现证：文案一写上去，
            #   t14 的 ⑥b 立刻红在自己的消息里）。措辞改成「不计入失败告警」。
            logger.warning(f"[runner-dropped] sid={sid} 落态前会话行已不在，本场按散会收（不计入失败告警）")
            self._forget(sid, terminal=True)
            return
        message = f"{type(exc).__name__}: {exc}"
        kind, human = self._fail_kind(exc)
        # 可 grep 的告警（C18②）：这是会话被打成 failed 的唯一出口。事件流是喂界面的，日志才是运维
        # grep 的对象——缺这一行时「点了允许然后整场死了」在日志里零痕迹（告警与重试是两回事：
        # 连接类失败本就由 `_retryable` 重发过，重发用尽之后必须留下响）。
        # ⚠ 前缀 `[session-failed]` 一字不改（s17 t9 的判据就钉着它），C116 只在**行尾加一个 kind 字段**：
        #   把别人的判据放宽不是修分类，是把分类藏回看不见。
        logger.error(f"[session-failed] sid={sid} kind={kind} {message}")
        session = self.store.update(sid, status=SessionStatus.failed,
                                    error=f"{human}（{message}）" if human else message,
                                    finished_at=_now())
        detail = traceback.format_exc(limit=6)
        # C182：死因码上 wire——`_fail_kind` 三族在界面上要分得开（前端留了 `.turnErrorCode` 槽
        # 三年没东西可放），码 = kind 同一来源，不发明第二套分类。
        self.bus.publish(sid, kind="error", code=kind, value=f"{human}\n{detail}" if human else detail)
        self._publish_status(session, session.error if human else message)
        self._forget(sid, terminal=True)

    # ---- 主流程 -------------------------------------------------------------
    def _make_chat(self, sid: str):
        """插话队列的装配出口：进程内 ChatQueue（默认）或 RedisChatQueue（S7 flag 开时注入工厂）。

        B6：装一个 `on_change` 接缝再交出去——「谁在队列里」只有队列自己知道
        （HTTP 线程投、图里 route 每轮取），别处猜都只能猜成第二个游标（§9 第 5 条当年写的
        「若需界面可见，接缝应做在 ChatQueue」就是这个口子）。工厂签名不动（它只认 sid），
        所以在这里补装属性，两台同名同属性。"""
        if self.chat_factory:
            chat = self.chat_factory(sid)
        else:
            from codeharness.runtime import ChatQueue
            chat = ChatQueue()
        chat.on_change = self._chat_notifier(sid)
        return chat

    def _chat_notifier(self, sid: str):
        def notify(action: str, items: list):        # 普通函数（桥接铁律，同 _make_sink）
            self.bus.publish(sid, kind="queue", name=action, value={"items": items})
        return notify

    async def _run(self, session: Session):
        sid = session.id
        from codeharness.provider.cost import CostManager

        chat = self.chats[sid] = self._make_chat(sid)
        cost_manager = self.costs[sid] = CostManager()
        project = self.projects[sid] = session.project_name or sid
        try:
            # 账本必须由 runner 建、传进图：图内各角色的 LLM 共用这一个实例，runner 上报时读的就是这同一个
            team, config, init = await self._prepare(session, project, cost_manager)
            self.graphs[sid] = (team, config)        # 快路径；丢了也能从 checkpointer 重建

            with self._session_ctx(sid):
                session = self.store.update(sid, status=SessionStatus.running, started_at=_now())
                self._publish_status(session, "team started")
                async for ev in team.astream_events(init, config, version="v2"):
                    self._translate(sid, ev)

            await self._settle(sid)
        except asyncio.CancelledError:                    # 必须 re-raise，否则僵尸协程
            session = self.store.update(sid, status=SessionStatus.stopped, finished_at=_now())
            self._publish_status(session, "stopped by user")
            self._forget(sid, terminal=True)
            raise
        except Exception as exc:
            self._fail(sid, exc)
        finally:
            self.tasks.pop(sid, None)

    def _sync_cost(self, sid: str):
        """每笔 LLM 调用落账后把账本合进会话态与事件流（GET 轮询与 SSE 各读一头）。
        ⚠ 读的是 `self.costs[sid]`——必须与图内 gateway 持同一个实例，双账本正是恒 0 的根因；
        快照可能比最后一笔晚到一步（on_chat_model_end 的回调先于网关落账），终态快照兜底。"""
        cm = self.costs.get(sid)
        session = self.store.get(sid)
        if cm is None or session is None:
            return
        cost = cost_snapshot(cm)
        self.store.set_cost(sid, cost, persist=True)
        status = str(session.status.value if hasattr(session.status, "value") else session.status)
        self.bus.publish(sid, kind="status",
                         value={"status": status, "error": session.error,
                                "cost": cost, "message": ""})

    def _trace_span(self, sid: str, node: str, slot=None):
        """N4 数据层：每笔 LLM 调用记一条 span（节点/token 增量/两桶成本增量/时刻）→ ch:trace:{sid}。
        `t0` 是派发时刻、`ft` 是首 token 时刻，来自 `_translate` 按 run_id 攒的在途表；
        采不到就是 null——前端据此**不显示**读数，而不是显示一个假的 0。
        trace 未注入（进程内默认）= 零开销直通。"""
        if self.trace is None:
            return
        cm = self.costs.get(sid)
        if cm is None:
            return
        t0, ft = slot if slot else (None, None)
        cur = (cm.total_prompt_tokens, cm.total_completion_tokens,
               round(cm.cost_usd, 6), round(cm.cost_cny, 6))
        prev = self._last_span.get(sid, (0, 0, 0.0, 0.0))
        self._last_span[sid] = cur
        # 一笔调用只会有一个币种在动（一个模型一套价），但两桶都发出去：
        # 前端据此各标各符号，而不是把两个数加回一列「成本」——那正是 C12 修掉的形状。
        self.trace.record(sid, {"node": node, "pt": cur[0] - prev[0], "ct": cur[1] - prev[1],
                                "cost_usd": round(cur[2] - prev[2], 6),
                                "cost_cny": round(cur[3] - prev[3], 6), "ts": time.time(),
                                "t0": t0, "ft": ft})

    # ---- astream_events 翻译（LLM 用量合流与打字机、interrupt，其余块走报道槽） ----
    def _role_signal(self, sid: str, ev: dict, start: bool):
        """C190（10-10）：把「某个角色开始干活 / 正在干什么 / 收工了」说上事件流——
        参照系 `run_started` / `run_phase` / `run_completed` 在本仓的对应物。

        为什么必须由**服务端**发而不是前端自己从块流倒推：块上带的 `role` 只有一个字符串，
        「这一趟开始了、这一趟结束了、他此刻在思考还是在执行动作」在协议里**没有对象**，
        于是前端只能拿一颗头部胶囊倒着扫块猜（`ConversationRoot.vue:195-200`），五角色并发就分不出人。
        车道是一颗**块**（`block="RoleLane"`），所以前端不需要第二套渲染管线：开块/改 meta/收口
        沿用 C188 那一个 reducer，刷新与「加载更早」整本重折时车道自己复原。

        形状现取三条（`tests/manual_role_node_shape_probe.py`，别照参照系想象）：本体是
        `name == langgraph_node == 角色名` 且 `checkpoint_ns` 为空；相位看内层节点名；
        `run_id` 整场共用 ⇒ 配对靠 `_lane_open` 那颗栈。
        """
        md = ev.get("metadata") or {}
        node = str(md.get("langgraph_node") or "")
        name = str(ev.get("name") or "")
        ns = str(md.get("checkpoint_ns") or "")
        names = self._node_names.get(sid) or set()
        if not names:
            return                                     # 还没装配过（单测直接喂事件）：不猜名单
        if node and node == name and not ns and node in names:
            stack = self._lane_open.setdefault(sid, {}).setdefault(node, [])
            if start:
                n = self._lane_seq.get(sid, 0) + 1
                self._lane_seq[sid] = n
                lane = f"lane-{n}"                     # 名字里不放角色：同一角色两次激活要两条车道
                stack.append(lane)
                self._lane_t0[(sid, lane)] = time.time()
                self.bus.publish(sid, kind="role", name="started", block="RoleLane", uuid=lane,
                                 role=node, value={"role": node, "phase": "observe"})
            elif stack:
                lane = stack.pop()
                ms = max(0, round((time.time() - self._lane_t0.pop((sid, lane), time.time())) * 1000))
                self.bus.publish(sid, kind="role", name="completed", block="RoleLane", uuid=lane,
                                 role=node, value={"role": node, "ms": ms})
            return
        if not start or ":" not in ns:
            return                                     # 相位只在开那一刻发；收口不重复发（重复＝噪声）
        role = ns.split(":", 1)[0]
        if role not in names or name not in ("observe", "think", "gate", "act"):
            return                                     # 别的内部节点（_route / LangGraph 包装…）不是相位
        stack = self._lane_open.get(sid, {}).get(role) or []
        if not stack:
            return                                     # 本体没开过车道：不发孤立相位，前端会画出一条没主的行
        self.bus.publish(sid, kind="role", name="phase", block="RoleLane", uuid=stack[-1],
                         role=role, value={"role": role, "phase": name})

    def _close_lanes(self, sid: str):
        """C190 的另一半，与 C172 同族：**取消 / 异常那两条路走不到 `on_chain_end`**。
        不收口就是界面上那个角色永远「在跑」——前端三处只认 `b.closed`（车道行的扫光、
        相位文本、状态色），而冷档回放同形（刷新也不会好）。`aborted` 带上，界面才敢说
        「这条没跑完」而不是假装收工。"""
        open_lanes = self._lane_open.get(sid) or {}
        for role, stack in list(open_lanes.items()):
            for lane in stack[:]:
                stack.remove(lane)
                t0 = self._lane_t0.pop((sid, lane), None)
                ms = max(0, round((time.time() - t0) * 1000)) if t0 else 0
                self.bus.publish(sid, kind="role", name="completed", block="RoleLane", uuid=lane,
                                 role=role, value={"role": role, "ms": ms, "aborted": True})
            if not stack:
                open_lanes.pop(role, None)

    def _translate(self, sid: str, ev: dict):
        kind = ev.get("event", "")
        rid = str(ev.get("run_id") or "")
        if kind == "on_chat_model_start":
            if NO_STREAM_TAG in (ev.get("tags") or []):
                # C177：调用方声明这一笔不上打字机 ⇒ **连 `meta` 都不建**（建了就是一个空扫光的行），
                # 兜底行登记表 `_stream_rows` 也不登记（散会时没人欠它收口）。计时照记：
                # span 与账本吃的还是同一本账，挡的是上屏不是计量。
                if rid:
                    self._call_t0[(sid, rid)] = [time.time(), None]
                    self._silent_runs[(sid, rid)] = True
                return
            if rid:
                self._call_t0[(sid, rid)] = [time.time(), None]
                self._prompts[(sid, rid)] = _prompt_text(ev.get("data") or {})
                self._blk_of[(sid, rid)] = self._live_blk.get((sid, self._owner(ev.get("metadata"))))
                # C130 的快照 + C174 的分槽：取**自己角色**手上开着的块；别人的块不再是这一笔的落点
            # 静默期要有东西可看：思考型模型在首 token 前实测要等 40~51 秒，而这段时间**没有**
            # 真文本可发——reasoning 增量拿不到（langchain-openai 1.5.1 的 `chat_models/base.py`
            # 模块头「API scope」明文：第三方私有字段如 `reasoning_content` 不被提取；09-28 现证
            # 逐片数 `additional_kwargs` 恒 0）。所以这里只把这一笔的流块**建起来**：发 `meta`
            # 而不是空 `content`，前端的 Think 行据此进 running 态扫光，而 `fts`（首 token 时刻）
            # 与成本读数一个都不动——拿静默期冒充首 token 就是假读数。
            node = ev.get("metadata", {}).get("langgraph_node", "")
            # 内核已经有块开着就不再另起一行——那一行本来就在那儿、就是 running 态。
            who = self._owner(ev.get("metadata"))            # C174：这一笔在哪个角色手上起跑的
            row = f"stream-{who or node}"                      # 兜底行也按角色分（改前都叫 stream-think，两角色并行就同屏交错）
            if not self._live_blk.get((sid, who)):
                self.bus.publish(sid, kind="report", block="Thought", uuid=row,
                                 name="meta", value={"streaming": node}, role=who or node)

                # C172：这颗行现在开着，记进散会要收的账（值先置 False：还没发过任何一个逐片）。
                # C178：键必须与**开行的 uuid 同源**（`row`）——写 `stream-{node}` 在生产形状下会分家：
                # 真图里 `who` 是角色名（C174 现证）而 `node` 是 think/act，于是开行发 `stream-Alice`、
                # 收口的 `only=` 也按 `who or node`，只有这张表按 node 登记 ⇒ 零逐片那一行散会收不了口。
                self._stream_rows.setdefault(sid, {})[row] = False
        elif kind == "on_chat_model_end":
            self._sync_cost(sid)
            used_kernel = bool(self._used_kernel_block.pop((sid, rid), None))
            silent = bool(self._silent_runs.pop((sid, rid), None))      # C177：这一笔全程没上过屏
            if rid:
                self._prose.pop((sid, rid), None)
                self._prompts.pop((sid, rid), None)
                self._blk_of.pop((sid, rid), None)      # C130：快照随笔走，收口即弃
            # 打字机流块（stream-{node}）到此收口，否则跑完了光标还在闪（S8 终验现形）。
            # 但**落进内核块的那一笔不替兜底行收口**：那一行这一笔压根没碰，收它等于替别人关
            # （离线工装现证过这条多余事件；孤立 end_marker 前端虽有守卫，长流里那行若被上一笔开过就会被无端关掉）。
            node = ev.get("metadata", {}).get("langgraph_node", "")
            if not (used_kernel or silent):      # 落进内核块的那一笔不替兜行收口，其余照旧（t3/t7 钉的就是它）；
                # C177：声明不上屏的那一笔同样不收——它这一笔压根没碰过兜底行，收它等于替同一节点里
                # 另一笔还开着的行关门。
                self._end_stream_rows(sid, only=f"stream-{self._owner(ev.get("metadata")) or node}")
            self._trace_span(sid, node, self._call_t0.pop((sid, rid), None) if rid else None)
        elif kind == "on_chat_model_stream":
            if (sid, rid) in self._silent_runs:      # C177：这一笔由调用方声明不上屏，逐片照旧进模型、只是不发布
                return
            # structured 的逐片 JSON 在这儿抽成散文（`_ProseStream` 的 docstring 记了为什么）；
            # 裸文本流原样透传，与改动前逐字一致。
            chunk = ev["data"]["chunk"]
            raw = getattr(chunk, "content", "")
            # 内容块形态（非 str）没有可抽的字符流，与改动前一致地跳过。
            text = raw if isinstance(raw, str) else ""
            if not text:
                return
            ps = self._prose.get((sid, rid))
            if ps is None:
                # 名单取 **start 时刻的落点快照**（C130）：块的 meta 一定先于这一笔的 start 到达
                # （`async with` 先开块再发调用），并行 Send 下别的角色后开的块也抢不走这一笔的名单。
                blk = self._blk_of.get((sid, rid))
                ps = self._prose[(sid, rid)] = _ProseStream(self._prompts.get((sid, rid), ""),
                                                            blk[2] if blk else None)
            piece = ps.feed(text)
            if not piece:
                return                    # 结构脚手架与短字段不上屏
            node = ev.get("metadata", {}).get("langgraph_node", "")
            slot = self._call_t0.get((sid, rid)) if rid else None
            # 首 token 只认第一个**有散文**的分片；没采到就是 None，不拿收口时刻凑一个假 TTFT。
            if slot is not None and slot[1] is None:
                slot[1] = time.time()
            # 落点 = 这一笔 start 时刻所在的块（C130 快照；并行 Send 下各投各的，不实时抢单槽），
            # 没有才落 `stream-{node}` 兜底（ponytail：兜底 uuid 按节点名，多角色并行且都没进
            # 内核块时仍会同屏交错——升级路径是按 run_id 派生 uuid，动前端契约，不在本件）；
            # 名字用 `live`：内核定稿是 `content`，两者在前端各占一格（正文 = tokens + live，
            # content 一到就把 live 清空），所以永远不会出现两份同屏。
            tgt = self._blk_of.get((sid, rid))
            if tgt:
                self._used_kernel_block[(sid, rid)] = True
                uid, btype = tgt[0], tgt[1]
                self.bus.publish(sid, kind="report", block=btype,
                                 uuid=uid, name="live", value=piece, role=node)
            else:
                row = f"stream-{self._owner(ev.get("metadata")) or node}"
                self.bus.publish(sid, kind="report", block="Thought",
                                 uuid=row, name="live", value=piece,
                                 role=self._owner(ev.get("metadata")) or node)
                self._stream_rows.setdefault(sid, {})[row] = True   # C173：有字才补分隔
        elif kind == "on_chain_start":
            self._role_signal(sid, ev, True)          # C190：角色开跑 / 相位
        elif kind == "on_chain_end":
            self._role_signal(sid, ev, False)         # C190：角色收工（配对靠 `_lane_open` 那颗栈）
        # 注意：这里**没有** `on_interrupt` 分支。旧实现有一条，靠它置 `awaiting_human` 并把审批卡
        # 推进活流——实测 langgraph 1.2.11 的 `astream_events(v2)` 只发 `on_chain_start/stream/end`，
        # 根本没有 interrupt 事件（A4 真模型那批取到的读数，PLAN §2 A4 行），那条分支永不触发，
        # 于是停在待批处的会话被 `_settle` 无条件写成 finished、graphs 被 pop、活流里也看不到卡。
        # 落态与推卡统一改问活图：`_interrupt_payload()` → `_park()`。

    def _publish_status(self, session: Session, message: str = ""):
        """用量快照只读：token 与成本照实报，没有任何预算上限字段。"""
        cost = {}
        cm = self.costs.get(session.id)
        if cm is not None:
            cost = cost_snapshot(cm)
            self.store.set_cost(session.id, cost, persist=True)
        self.bus.publish(session.id, kind="status",
                         value={"status": str(session.status.value if hasattr(session.status, "value")
                                               else session.status),
                                "error": session.error, "cost": cost, "message": message})
