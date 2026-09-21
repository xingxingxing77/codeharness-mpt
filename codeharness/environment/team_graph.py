"""团队编排：源 base_env.py + team.py 的 LangGraph 化。
publish_message(:176) → route()；run(:198) → 图执行；is_idle(:230) → route 无目标即 END；
hire(:83) → 组图；run(n_round)(:123) → recursion_limit；
serialize/deserialize(:59/:67) → checkpointer（一行不写）。"""
import operator
from typing import Annotated, TypedDict
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from codeharness.const import MESSAGE_ROUTE_TO_ALL, MESSAGE_ROUTE_TO_NONE, MESSAGE_ROUTE_TO_SELF, RequirementTag
from codeharness.schema import Message


def merge_dicts(a: dict, b: dict) -> dict:
    """并行 Send 时 dict 合并 reducer（memories 按角色名合并，防覆盖）"""
    return {**a, **b}


class UnknownRecipient(ValueError):
    """委派/路由指向一个不在装配里的收件人——**当场抛，不再静默丢**（C1-d 拍板）。

    为什么必须抛：LangGraph 对未知节点只打一行 `Ignoring unknown node name PM` 然后
    **整场零次 LLM 调用**（docs/对照5:95 与本仓 `_default_agents` 的注释都记过），
    表现是"会话正常跑完、什么都没产出"——静默比报错贵得多：用户等到结束才发现没结果，
    而日志里只有一行别人家的 warning。抛出去会走 runner 的 `_fail`，会话标 failed、
    error 文本可读，门禁也终于能断言这一格。
    `<all>` / `<none>` / `<self>` 是路由标记不是角色名，不在此列（`<all>` 本仓刻意不广播，
    见 route 里的注释；把它当错误会把所有默认值消息全炸掉）。"""


class TeamState(TypedDict):
    messages: Annotated[list, operator.add]        # 全局黑板 = env.history
    memories: Annotated[dict, merge_dicts]         # 每角色私有记忆（checkpointer 持久化）
    seen: int                                      # 路由游标：黑板已被消费到的条数（router 写，C13）
    undelivered: list                              # 本超步新增、还没投递的那一截（router 算，route 只读）
    debug_rounds: int                              # QA 修复回路上限（参考速查 §2）
    team_rounds: int                               # 委派↔回报来回上限（C1-②b，防队长-成员活循环）
    finished: bool
    # ⚠ 原来的 `round: int` 已删（C14）：三处 init 写 0 后全仓零读零写，与 C2 的 `docs` 同族。
    #    `seen`/`undelivered` **不进 init**——它们由 router 现算，且必须跟着 checkpointer 走：
    #    跑完的会话再 start 时 messages 是「追加」而这两个键若被 init 重置成全量重投。
    #    复燃守卫见 `tests/s3b_runtime.py::t15`。


# ---- 真实 SOP 订阅表（= 各角色 _watch + 参考速查全图；经典线） ----
SOP = {
    RequirementTag.USER_REQUIREMENT:   ["PM"],
    RequirementTag.WRITE_PRD:          ["Architect"],
    RequirementTag.WRITE_DESIGN:       ["PMManager"],
    RequirementTag.WRITE_TASKS:        ["Engineer"],
    RequirementTag.SUMMARIZE_CODE:     ["QA"],
    RequirementTag.WRITE_CODE_PLAN_AND_CHANGE: ["Engineer"],
    RequirementTag.FIX_BUG:            ["Engineer"],
    RequirementTag.DEBUG_ERROR:        ["Engineer"],   # QA 修复后回 Engineer 确认
}


# ---- 黑板→上下文自动装配（源项目 i_context 的等价机制） ----
# key = cause_by tag；value = assembler(msg, state) -> list[Message]（一条消息 = 一个 Send 载荷）。
# 这是 e2e 里手写 wrapper 的产品化：WriteTasks 拆任务多 Send、QA 拿到 TestingContext。
def _wire_write_tasks(msg: Message, state: dict) -> list[Message]:
    """WriteTasks 的任务清单 → 每个文件一条 Engineer 载荷（源 i_context=CodingContext 语义）

    分工照源：`content` = 给 agent 看的自然语言指令，`instruct_content` = 结构化上下文。
    任务指令不要塞进 instruct_content——CodingContext 没这个字段（源也没有），
    BaseSerialization 的 extra="forbid" 会拒绝它。"""
    tasks = (msg.instruct_content or {}).get("task_list", [])
    out = []
    for t in tasks:
        out.append(Message(content=t.get("instruction", "") or msg.content, role="user", cause_by=msg.cause_by,
                           sent_from=msg.sent_from,
                           instruct_content={"filename": t.get("filename", "")},
                           instruct_schema="CodingContext"))
    return out


def _wire_summarize_code(msg: Message, state: dict) -> list[Message]:
    """SummarizeCode → QA：从产物仓取最新 src 文件装配 TestingContext（源 qa_engineer.py:92 语义）"""
    from codeharness.document_store.artifact_store import ArtifactStore
    from codeharness.const import RepoName
    store = ArtifactStore.active()
    files = store.all_files(RepoName.SRC)
    if not files:
        return [msg]
    latest = max(files, key=lambda f: (store.root / RepoName.SRC / f).stat().st_mtime)
    code_doc = {"filename": latest, "root_path": RepoName.SRC,
                "content": (store.root / RepoName.SRC / latest).read_text(encoding="utf-8",
                                                                          errors="replace")}
    return [Message(content=msg.content, role="user", cause_by=msg.cause_by, sent_from=msg.sent_from,
                    instruct_content={"code_doc": code_doc}, instruct_schema="TestingContext")]


def _wire_delegation(msg: Message, state: dict) -> list[Message]:
    """队长的委派聚合件 → 每个成员一条载荷（C1-②，源 publish_team_message 的「一发一投」）。

    RoleZero 的正常产出也是 cause_by=RunCommand，靠 `instruct_schema` 认委派，其余原样透传。
    每条载荷自带 `send_to={成员}`，route 据此定向投递——否则两名成员会互相收到对方的任务。"""
    if msg.instruct_schema != "TeamDelegation":
        return [msg]
    delegations = (msg.instruct_content or {}).get("delegations") or []
    return [Message(content=d.get("instruction", ""), role="user", cause_by=msg.cause_by,
                    sent_from=msg.sent_from, send_to={d.get("member", "")})
            for d in delegations] or [msg]


CONTEXT_WIRING = {
    RequirementTag.WRITE_TASKS: _wire_write_tasks,
    RequirementTag.SUMMARIZE_CODE: _wire_summarize_code,
    RequirementTag.RUN_COMMAND: _wire_delegation,
}


def make_route(sop: dict, agents: dict, wiring: dict | None = None, stats: list | None = None):
    """= 源 base_env.publish_message(:176) 的路由语义 + i_context 装配，工厂化便于测试

    `stats`：每传一条消息记一条 {"cause_by", "activated", "roles"}，用于验证
    **精准激活**（订阅式路由的本意就是每轮只唤醒相关角色，而不是全员轮询）。"""

    def route(state: TeamState):
        if state.get("finished"):
            return END
        if not state["messages"]:
            return END
        # **本超步新增的消息全投，不止最后一条**（C13）。旧写法只看 `messages[-1]`：
        # 一次节点运行可以产多条（RoleZero 的报告 + 委派聚合件），一个聚合件也能给同一成员
        # 拆出多条任务，而它们都落在同一个超步里 → 只有排在最后的那条被投，其余永久卡在黑板上。
        # A4 那批真跑实测过这条：队长一轮攒 15 条委派 → 15 个并发 Send → 14 条回报没人收。
        # 切片由 router 节点算好写进 `undelivered`（条件边不能写状态，s16 t1 的实测口径）；
        # 首轮（START 后第一次进 router 之前没有 undelivered）退回「只看最后一条」的旧形状。
        batch = state.get("undelivered")
        if batch is None:                        # 首轮/老线程没这个键：退回「只看最后一条」的旧形状
            batch = state["messages"][-1:]
        markers = (MESSAGE_ROUTE_TO_SELF, MESSAGE_ROUTE_TO_ALL, MESSAGE_ROUTE_TO_NONE)
        w = wiring or CONTEXT_WIRING                    # 别名：route 内赋值会遮蔽闭包变量
        sends = []

        for last in batch:
            # <self> 自投递（QA 的 WriteTest→RunCode→DebugError 内环不广播；B9 的错误/拒绝回喂同路）
            if MESSAGE_ROUTE_TO_SELF in last.send_to:
                if last.sent_from not in agents:
                    raise UnknownRecipient(
                        f"<self> 回喂的目标节点 {last.sent_from!r} 不在装配里（在册：{sorted(agents)}）")
                # 自环一律计数、不挑 cause_by：B9 回喂的错误/拒绝消息 cause_by=action.name，
                # 挑白名单会漏掉它——实测同一失败动作激活 12 次打到 recursion_limit
                # （A4 之前那轮 b9 探针）。刹车从前的 `return END` 改成 `continue`：
                # 一条被刹住的链不该顺带把同批其它消息与插话一起丢掉。
                if state.get("debug_rounds", 0) >= 3:
                    continue
                sends.append(Send(last.sent_from, {"_inbox": [last]}))
                if stats is not None:
                    stats.append({"cause_by": last.cause_by, "activated": 1, "roles": len(agents)})
                continue

            # 目标 = 订阅表命中的角色 ∪ 消息里显式指名的角色。两边**都要求收件人真在装配里**，
            # 对不上就地抛 UnknownRecipient（C1-①；静默丢掉就是 LangGraph 那句
            # "Ignoring unknown node name" + 整场零模型调用）。
            # ⚠ send_to 的默认值是 <all>，这里**刻意不做广播**：多数 Action 不显式设 send_to，
            # 一旦把 `<all>` 当广播，每个动作都会唤醒全部角色，正好毁掉订阅式路由的精准激活。
            # 路由标记（<all>/<none>/<self>）不是角色名，不参与"存不存在"的判定。
            targets = []
            for name in sop.get(last.cause_by, []):
                if name not in agents:
                    raise UnknownRecipient(f"SOP 订阅表把 {last.cause_by} 派给不在装配里的角色 "
                                           f"{name!r}（在册：{sorted(agents)}）")
                targets.append(name)
            for name in sorted(last.send_to):
                if name in markers or name == last.sent_from:
                    continue
                if name not in agents:
                    raise UnknownRecipient(f"消息具名投递给不存在的角色 {name!r}"
                                           f"（发件人 {last.sent_from!r}，在册：{sorted(agents)}）")
                if name not in targets:
                    targets.append(name)

            # 死循环刹车（按消息判，不按"最后一条"判）：修复回路上限防 QA↔Engineer，
            # 委派↔回报来回上限防队长-成员互相重派（计数都在 router 节点写）。
            if last.cause_by == RequirementTag.DEBUG_ERROR and state.get("debug_rounds", 0) >= 3:
                continue
            if getattr(last, "instruct_schema", "") in ("TeamDelegation", "TeamReport") \
                    and state.get("team_rounds", 0) >= 12:
                continue

            # 上下文装配按 cause_by 只做一次，多目标共用（装配要读产物仓，别按目标重复读盘）
            payload_msgs = w.get(last.cause_by, lambda m, s: [m])(last, state) if targets else []
            # 装配器产出的载荷可以自带收件人（委派的「一人一条、各投各的」）；原始消息与非具名载荷
            # 仍按 targets 全发。**收件人只从 targets 里收窄、不新增**，所以 C1-① 的 UnknownRecipient
            # 判定绕不过去；收窄后为空 = 这条只发给发件人自己（源 publish_team_message 里就是直接 return）。
            msg_sends = []
            for m in payload_msgs:
                named = set() if m is last else m.send_to.difference(markers)
                tgts = [t for t in targets if t in named] if named else targets
                msg_sends.extend(Send(t, {"_inbox": [m]}) for t in tgts)
            sends.extend(msg_sends)
            if stats is not None:
                stats.append({"cause_by": last.cause_by, "activated": len({s.node for s in msg_sends}),
                              "roles": len(agents)})

        # ---- 运行中插话（前端 InputCard "追问"；空目标 = TeamLeader） ----
        from codeharness.runtime import CHAT_SINK
        chat = CHAT_SINK.get()
        if chat:
            for content, send_to in chat.drain():
                recv = send_to or chat.default_target
                if not recv:
                    continue                      # 压根没目标（会话还没有 entry_role）——不是"名字写错"，另一回事
                if recv not in agents:
                    # 插话与委派**不同判**（用户 2026-09-21 定的档位）：这里丢那一条消息，
                    # 不抛——为一根错名字的角色赔上整场已烧的用量不划算，且端点早就按
                    # `session.roles` 422 过，走到这一步只可能是跨 worker 的陈旧队列或 ext_api 直投。
                    # 但不许静默：loguru 一条 WARNING + stats 里记一笔（门禁据此断言"丢了且留了痕"）。
                    from codeharness.logs import logger
                    logger.warning(f"插话指名投给不存在的角色 {recv!r}，该条已丢弃（在册：{sorted(agents)}）")
                    if stats is not None:
                        stats.append({"cause_by": "chat-dropped", "activated": 0, "roles": recv})
                    continue
                sends.append(Send(recv, {"_inbox": [Message(
                    content=content, cause_by=RequirementTag.USER_REQUIREMENT, sent_from="user")]}))

        # 每超步一条 stat 的旧形状已改成**每条消息一条**（与 C13 同批）：门禁按 stats 数激活次数，
        # 一批多条时旧的"只看最后一条"读数会把漏投伪装成精准。
        if not sends:
            return END                                 # 全批都没订阅者、也没插话可投 = 散会
        return sends

    return route


def build_team(agents: dict, checkpointer=None, sop: dict | None = None, stats: list | None = None):
    """= 源 team.hire(:83)。agents: {name: Agent|RoleZero}，均提供 as_node(name) 接口

    ⚠ 默认 checkpointer 是内存型：内核自测不落盘。持久化断点由调用方（server runner）
    经 `environment/checkpoint.py::make_checkpointer` 显式注入。
    `stats` 传一个列表即可拿到每轮实际激活的节点数（见 make_route）。"""
    # ⚠ `sop or SOP` 会把「显式传空表」（= 这场没有订阅，只靠具名投递）吃成 falsy、
    # 悄悄退回经典表——两个不同的东西共用一个 falsy 值，判据想隔离具名路时会中这个坑。
    route = make_route(SOP if sop is None else sop, agents, stats=stats)
    g = StateGraph(TeamState)

    async def router(state: TeamState):
        """汇聚虚节点：所有产出流回这里，由它算出「本超步新增了哪些消息」再交给 route 路由。

        ⚠ **状态写入的唯一入口在这里**：节点返回值才是状态提交口，条件边函数（`route`）里
        `state[k] = v` 会被丢掉（langgraph 1.2.11 实测，见 `tests/s16_route_state.py::t1`）——
        原先写在 route 里的那道 `>= 3` 闸恒不生效，QA↔沙箱 / QA↔Engineer 的活循环只靠
        `recursion_limit=60` 兜底，撞上就把整个会话打成 failed。
        计数判据与 route 的刹车严格一致：`<self>` 自环一律一轮，DEBUG_ERROR 广播环一轮。

        C13 的两件也在这里：`seen`=黑板已消费的条数、`undelivered`=本次新增的那一截。
        route 只读不算（它写不了状态），而「新增」必须有游标才能算，所以切片在这里做。
        游标**不进 init**：跑完的会话再 start 时 `messages` 是追加，若把 seen 重置成 0，
        整条历史会被重新投递一遍。"""
        msgs = state.get("messages") or []
        out: dict = {"seen": len(msgs), "undelivered": msgs[state.get("seen", 0):]}
        last = msgs[-1] if msgs else None
        if last is None:
            return out
        if MESSAGE_ROUTE_TO_SELF in last.send_to or last.cause_by == RequirementTag.DEBUG_ERROR:
            out["debug_rounds"] = state.get("debug_rounds", 0) + 1
        # 委派与回报各算一次来回（聚合件的标记在拆包前，回报标记在消息本身上）
        if getattr(last, "instruct_schema", "") in ("TeamDelegation", "TeamReport"):
            out["team_rounds"] = state.get("team_rounds", 0) + 1
        return out

    g.add_node("router", router)
    for name, agent in agents.items():
        node_name, node_fn = agent.as_node(name)
        g.add_node(node_name, node_fn)
        g.add_edge(node_name, "router")

    g.add_edge(START, "router")
    g.add_conditional_edges("router", route)
    return g.compile(checkpointer=checkpointer or InMemorySaver())
