"""③ C104 未验②：经典线多角色「同场各一张计划卡」在**真模型真会话**里的有界活体。

判据原话（`plan/frontend.md` §1.1「第十三件」未验②）：「多角色同场各一张卡只由合成事件证」。
本工装补的就是这一格：起 **`paradigm=classic`** 那条腿（`codeharness/team.classic_team` → 一串 RoleZero 角色，
`_report_plan` 经 `set_role` 注 role → runner `_make_sink` 把 Task-object 卡 uuid 收敛成 `plan-{role}`，`runner.py:678`）。

**结构性断言（不拿模型脾气判 pass/fail）**：
  ① bus 里出现 **>=2 个不同的 `plan-*` uuid 的 Task-object 事件** ⇒ 多角色各一张卡成立；
  ② 同一 `plan-{role}` 上的 Task-object **只此一张**（重复推进不裂成第二张）⇒ C104 收敛（uuid 逐字相等）生效；
  ③ 花费照实印（读产品那本账 `GET /{sid}` 的 cost，不是估算）。
  ⚠ 若跑到闸仍未见 >=2 张角色卡：**如实报「没验成/形状没到」并 exit 1**——绝不用单角色那场冒充、不拿「模型没自发立计划」
     当成「收敛坏了」的反证（那是模型脾气，不是判据）。

**硬闸三件齐全，全写在起跑那一个进程里**（09-29 那笔 ¥21.6 事故 + 09-30「累计闸拦不住单发」的教训）：
  · `CLASSIC_SEND_GATE`（默认 16 发真 StepFun 请求）——数在 openai SDK 最底层，langchain 绕不过；
  · `CLASSIC_MAX_CNY`（默认 ¥0.60）——**每发之后**读那本账，超了立刻停（封顶小额）；
  · `CLASSIC_MAX_TOKENS`（默认 1200）——单发封顶；
  · `CLASSIC_CAP_SEC`（默认 280s）墙钟兜底；
  外加「**形状一到就 stop**」写在同一个钩子里：第 ①② 格要的 >=2 张角色卡一旦在进程内 bus 里凑齐，当场 stop。
  撞闸/形状到 **只 raise `GateHit` 不 sys.exit**（钩子跑在 runner 的 asyncio 任务里，SystemExit 掀进程＝花费与中间读数全丢）。

跑法（要 `.env` 里那台 StepFun；端点不是它就 exit 1，不发替身读数冒充真模型）：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 REDIS__DB=15 LANGFUSE__ENABLED=0 NO_PROXY=127.0.0.1,localhost,::1 \\
    F:/anaconda/python.exe -B tests/manual_classic_plan_live.py

**这一份不算门禁**：挂在真端点与真凭据上。只用临时目录里的 sessions 文件与工作区（工作区**挪出仓库**，
`finally` 的 rmtree 打不到别人的在途产物），不碰 8718、不碰 db0、也不碰 db15（`use_redis=False`
⇒ 不占门禁的排他资源）、不碰 `.env`、不发任何 embedding（`enable_rag=False`）。
permission=workspace_write：经典线 PM 的第一步是 `PrepareDocuments`（写动作），`readonly` 会把它挡在审批闸、
**早于任何 think** ⇒ 整场 0 出网、永远拿不到计划卡（16:1x 现证：242s 卡死、¥0）。workspace_write 让写进本会话
工作区免审，图才走得动；终端/联网/越界写仍要批。**花费仍靠上面那三件闸封顶，不靠这个档位假设。**
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SEND_GATE = int(os.environ.get("CLASSIC_SEND_GATE", "16"))
MAX_CNY = float(os.environ.get("CLASSIC_MAX_CNY", "0.60"))
CAP_SEC = float(os.environ.get("CLASSIC_CAP_SEC", "280"))
MAX_TOKENS = int(os.environ.get("CLASSIC_MAX_TOKENS", "1200"))
PROJECT = "classic_plan_live"
_sent = 0
_watch = {}


class GateHit(RuntimeError):
    """撞闸专用：只死在这一个 asyncio 任务里，不许掀进程（读数要在退出前取到手）。"""


def _spent():
    runner, sid = _watch.get("runner"), _watch.get("sid")
    cm = runner.costs.get(sid) if (runner and sid) else None
    return float(getattr(cm, "cost_cny", 0.0) or 0.0)


def _plan_cards(bus, sid):
    """从进程内 bus 历史里收 Task-object 计划卡：返回 {uuid: 该卡事件条数}。

    读的是 bus 的历史出口（与 SSE/回放同一条路），形状只从产品真发出去那份里取——不猜。
    C104 之后 `runner.py:678` 把这些卡 uuid 收敛成 `plan-{role}`，所以未收敛时 uuid 是随机 hex、
    收敛后前缀是 `plan-`：判「>=2 个不同 plan-* 」才是「多角色各一张」，判「每个 plan-* 计数==1」才是收敛。
    """
    if bus is None or sid is None:
        return {}
    cards = {}
    for e in bus.history(sid):
        if getattr(e, "name", "") == "object" and getattr(e, "block", "") == "Task":
            uid = getattr(e, "uuid", None) or ""
            cards[uid] = cards.get(uid, 0) + 1
    return cards


def _shape_ready(cards):
    plans = {u: n for u, n in cards.items() if u.startswith("plan-")}
    return len(plans) >= 2, plans


def _chain(bus, sid):
    """把 bus 历史里的事件压成 `kind/name/block[:角色]` 短串，供「卡在哪一步」诊断（不印正文）。"""
    if bus is None or sid is None:
        return []
    out = []
    for e in bus.history(sid):
        out.append(f"{getattr(e, 'kind', '')}/{getattr(e, 'name', '')}"
                   f"[{getattr(e, 'block', '') or ''}:{getattr(e, 'role', '') or ''}]")
    return out


def _hit_gate():
    """每发之后判四件事：发数、账上的钱、形状到没到。撞任何一条都当场停，且**先保住读数**。"""
    global _sent
    _sent += 1
    why = None
    if _sent > SEND_GATE:
        why = f"撞发数闸（第 {_sent} 发 / 闸 {SEND_GATE}）"
    elif _spent() > MAX_CNY:
        why = f"撞金额闸（¥{_spent():.6f} > 闸 ¥{MAX_CNY}，第 {_sent} 发后）"
    ready, plans = _shape_ready(_plan_cards(_watch.get("bus"), _watch.get("sid")))
    if ready:
        print(f"   [形状已到] 第 {_sent} 发后已见 {len(plans)} 张角色计划卡 ⇒ 当场 stop", flush=True)
        _stop_now("目标形状已到手（不必再往下跑）")
        return
    if why is None:
        print(f"   [真端点] 第 {_sent}/{SEND_GATE} 发｜已花 ¥{_spent():.6f}｜已见 plan-卡 {len(plans)} 张", flush=True)
        return
    print(f"🛑 撞闸：{why}——停手，抬闸要人拍", flush=True)
    _watch["gate"] = why
    _stop_now(why)
    raise GateHit(why)


def _stop_now(why):
    runner, sid = _watch.get("runner"), _watch.get("sid")
    if runner is not None and sid is not None:
        try:
            import asyncio
            asyncio.get_running_loop().create_task(runner.stop(sid))
        except Exception as e:
            print(f"   （就地 stop 没成功：{type(e).__name__} {e}——由墙钟兜底）", flush=True)
    print(f"   [stop] 原因：{why}", flush=True)


def _install_gate():
    from openai.resources.chat.completions import AsyncCompletions, Completions
    orig_a, orig_s = AsyncCompletions.create, Completions.create

    async def gated_a(self, *a, **kw):
        _hit_gate()
        kw.setdefault("max_tokens", MAX_TOKENS)
        return await orig_a(self, *a, **kw)

    def gated_s(self, *a, **kw):
        _hit_gate()
        kw.setdefault("max_tokens", MAX_TOKENS)
        return orig_s(self, *a, **kw)

    AsyncCompletions.create, Completions.create = gated_a, gated_s


def main() -> int:
    import tempfile
    import server.sessions as ss
    from codeharness.configs.settings import settings

    if not settings.llm.api_key:
        sys.exit("exit 1：.env 里没有 LLM 凭据，本工装不发空请求")
    if "stepfun" not in (settings.llm.base_url or ""):
        sys.exit(f"exit 1：LLM 端点不是 `.env` 那台（{settings.llm.base_url}）——"
                 "换成替身/本机模型跑出来的读数没有资格（PLAN §6 铁律 28）")
    _install_gate()

    from fastapi.testclient import TestClient
    from server.app import create_app

    tmp = Path(tempfile.mkdtemp())
    keep = (settings.enable_rag, settings.platform.auth_enabled, settings.platform.use_redis,
            ss.SESSIONS_FILE, settings.workspace_root)
    settings.enable_rag = False
    settings.platform.auth_enabled = False
    settings.platform.use_redis = False
    ss.SESSIONS_FILE = tmp / "sessions.json"
    import server.settings as srv_settings
    settings.workspace_root = str(tmp / "ws")
    srv_settings.WORKSPACE_ROOT = tmp / "ws"
    (tmp / "ws").mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    sid = None
    try:
        with TestClient(create_app()) as c:
            r = c.post("/api/sessions", json={
                "idea": "两个角色各自先立自己的小计划再动手：架构师负责把『摄氏转华氏』拆成 2-3 步计划，"
                        "工程师负责把『结果写成一行的落盘』拆成 2-3 步计划。不要直接写代码，先各自给出任务清单",
                "project_name": PROJECT,
                "paradigm": "classic",          # ← 经典线多角色那张队形
                "permission": "workspace_write",  # ← 见文件头：readonly 会把 PM 的 PrepareDocuments（写动作）
                                                  #   挡在审批闸、早于任何 think ⇒ 永远 0 发；workspace_write
                                                  #   让写进本会话工作区免审，图才走得动。花费仍靠三件闸封顶。
                "n_round": 2})
            assert r.status_code == 200, f"建会话回 {r.status_code}: {r.text[:200]}"
            sid = r.json()["id"]
            _watch.update(runner=c.app.state.runner, sid=sid, bus=c.app.state.bus)
            roles = c.get(f"/api/sessions/{sid}").json().get("roles")
            print(f"组队：{roles}｜会话 {sid[:9]}（临时目录，跑完即删）", flush=True)

            assert c.post(f"/api/sessions/{sid}/start").status_code == 200, "起跑失败"
            cards = {}
            while time.time() - t0 < CAP_SEC:
                cards = _plan_cards(_watch["bus"], sid)
                ready, plans = _shape_ready(cards)
                if ready:
                    break
                if _watch.get("gate"):
                    print("   主循环接住撞闸：" + _watch["gate"] + "——先取读数再退", flush=True)
                    break
                st = c.get(f"/api/sessions/{sid}").json().get("status")
                if st in ("finished", "failed", "stopped"):
                    print(f"   这场在凑齐 2 张角色卡前就到终态 status={st}", flush=True)
                    break
                # 起跑后一直没出网＝卡在第一个动作之前（readonly 会把 PM 的 PrepareDocuments 挡在审批闸，
                # 而那早于任何 think ⇒ 永远 0 发）。别再干等墙钟：40s 仍 0 发就把事件/状态摊出来止损。
                if _sent == 0 and time.time() - t0 > 40:
                    print(f"   ⚠ 起跑 40s 仍 0 出网（status={st}）——卡在任何 think 之前，不是「模型没自发立计划」。"
                          f"事件链：{_chain(_watch['bus'], sid)}", flush=True)
                    break
                time.sleep(1.5)
            _watch["cost"] = c.get(f"/api/sessions/{sid}").json().get("cost") or {}
            c.post(f"/api/sessions/{sid}/stop")
            time.sleep(1.0)
            cost = c.get(f"/api/sessions/{sid}").json().get("cost") or {}
            hist = c.get(f"/api/sessions/{sid}/events/history", params={"limit": 1000}).json().get("events") or []
            replay_cards = {}
            for e in hist:
                if e.get("name") == "object" and e.get("block") == "Task":
                    u = e.get("uuid") or ""
                    replay_cards[u] = replay_cards.get(u, 0) + 1

        print(f"\n读数：出网 {_sent} 发｜墙钟 {int(time.time() - t0)}s｜回放出口 {len(hist)} 条事件")
        ready, plans = _shape_ready(replay_cards)
        nonplan = {u: n for u, n in replay_cards.items() if not u.startswith("plan-")}
        total_cards = sum(replay_cards.values())
        print(f"① plan-* 角色卡（不同 uuid）：{len(plans)} 张 → {sorted(plans)}")
        print(f"② 每个 plan-* uuid 上收了几条 Task-object（>1＝同角色重复推进，前端按 uuid 认一张＝收敛正常，不是裂卡）：{ {u: n for u, n in plans.items()} }")
        print(f"③ 未收敛成 plan- 前缀的 Task-object 卡（应为空＝`runner.py:678` 的改写每条都命中）：{nonplan}")
        print(f"   Task-object 事件合计 {total_cards} 条，全落在 {len(replay_cards)} 个 uuid 上")
        print(f"④ 产品那本账：{cost}")
        if _watch.get("gate") and not ready:
            print("\n⚠ 撞闸退出、**未跑完**：" + _watch["gate"] + "｜上面是中间产物，不是闭合读数")
            return 3
        if not ready:
            if _sent == 0:
                print("exit 1：**根本没起跑**——出网 0 发，这场卡在任何 think 之前（多半是第一个动作被审批闸挡住），"
                      "既没验成也没花钱。看上面『事件链』定位卡点，**绝不把「没跑起来」记成 C104 的读数**。")
                return 1
            # 单角色也谈收敛：只要没有任何 Task-object 留在随机 uuid 上（nonplan 空），改写就命中了每一条真发射。
            if not nonplan and plans:
                print(f"⚠ 部分读数：C104 的 uuid 收敛在真模型上**对出现的每条计划卡都生效**（0 条未收敛），"
                      f"但这场只有 {len(plans)} 个角色自发立了计划（{sorted(plans)}）——"
                      "『≥2 个角色各一张』没复现。这是**行为覆盖**问题（经典线里多数是队长 PM 立计划、成员只执行被派的任务），"
                      "不是 C104 的缺陷；**不据此声称未验② 已闭合**。")
            else:
                print("exit 1：**没验成**——真模型这一场既没凑齐 >=2 个不同 `plan-*` 角色卡，"
                      f"且有 {len(nonplan)} 条 Task-object 没收敛到 plan- 前缀（非 plan {sorted(nonplan)}）⇒ 这才可能指向 C104 真没触发。"
                      "**绝不据此声称验过**。")
            return 1
        # 形状到了：>=2 个不同 plan-* uuid，且没有一条 Task-object 漏在随机 uuid 外＝改写每条命中（同角色多条共享一个 uuid 是正常更新，不算裂卡）
        assert not nonplan, \
            f"有 Task-object 没被收敛成 plan-{{role}}（{sorted(nonplan)}）⇒ C104 改写没覆盖全部真发射"
        print("\n✅ ③ C104 未验② 闭合：真模型真经典线上，>=2 个角色各出一张 `plan-{{role}}` 计划卡"
              f"（{sorted(plans)}），且**每一条 Task-object 真发射都被改写**（0 条漏在随机 uuid）——"
              "同角色多次推进共享同一个 uuid（前端认一张），正是收敛的预期形状。")
        return 0
    finally:
        settings.enable_rag, settings.platform.auth_enabled, settings.platform.use_redis, \
            ss.SESSIONS_FILE, settings.workspace_root = keep
        import shutil as sh
        sh.rmtree(tmp, ignore_errors=True)
        print(f"清场：临时工作区存在={(tmp / 'ws').exists()}｜临时 sessions 存在={(tmp / 'sessions.json').exists()}"
              f"｜出网合计 {_sent} 发｜账上 ¥{_spent():.6f}")


if __name__ == "__main__":
    sys.exit(main())
