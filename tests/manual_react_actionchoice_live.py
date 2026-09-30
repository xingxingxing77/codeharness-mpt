"""欠账③：**`ActionChoice` 那道名单到底有没有在真模型上走过**（真模型、真装配、花真钱）。

判据原话（`plan/frontend.md` 第八件 未验边界②）：「名单挂在 `_think` 那笔 structured 调用上，
而 09-29 18:1x 那场活体走的是 BY_ORDER 档 ⇒ 第八件这道名单至今没有活体走过，证据只是 t12⑩ 两格离线读数」。
本工装补的就是这一格：起 **`paradigm=react`** 那一条腿（`server/runner.py` 的 react 支 → `team.react_assembly`
⇒ 全员 `react_mode="REACT"` ⇒ `Agent._think` 真调 `structured(ActionChoice)`），
看那块开块时的 `meta` 有没有把名单带过 wire、逐片有没有只出 `thought`。

四格读数（**结构性断言，不拿模型脾气判 pass/fail**）：
  ① 有一个 `meta` 带 `type=="react"` 且 `prose_fields==["thought"]` ⇒ 名单过了 wire；
  ② 该 uuid 上的 `live` 逐片里，名单外的东西为 0（判的是「整片就是一个动作名/枚举值/裸路径」这种**成片形状**，
     不是子串命中——门控按字段不按词，散文里自己提到 `src/` 是允许发出的）；
  ③ 定稿 `content` 一到，那一块的 `live` 与 `content` 是两条事件（一次性出现，设计意图）；
  ④ 花费照实印（读产品那本账 `GET /{sid}` 的 cost，不是估算）。

三道闸（**都写在起跑的那一个进程里**，撞闸 exit 3 印「跑到第几发/已花多少」）：
  · `REACT_SEND_GATE`（默认 14 发真 StepFun 请求）——数在 openai SDK 最底层，langchain 也绕不过；
  · `REACT_MAX_CNY`（默认 ¥0.08）——**每发之后**读那本账，超了立刻停；
  · `REACT_CAP_SEC`（默认 300s）墙钟；
  外加一条「**形状一到就停**」也写在同一个钩子里：第 ①② 格要的事件一旦在进程内 bus 里出现，当场
  `stop`，不靠外面轮询。09-29 那笔 ¥21.615805 的事故就是这么来的：判停写在轮询侧、
  而起跑与第一次轮询之间被我插进了人工步骤。

跑法（要 `.env` 里那台 StepFun；端点不是它就 exit 1，不发替身读数冒充真模型）：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 REDIS__DB=15 LANGFUSE__ENABLED=0 NO_PROXY=127.0.0.1,localhost,::1 \\
    F:/anaconda/python.exe -B tests/manual_react_actionchoice_live.py

**这一份不算门禁**：挂在真端点与真凭据上。只用临时目录里的 sessions 文件与工作区（09-30 起工作区**挪出仓库**，
`finally` 的 rmtree 因此打不到别人的在途产物），不碰 8718、不碰 db0，也不碰 db15（`use_redis=False`
⇒ 不占门禁的排他资源）、不碰 `.env`、不发任何 embedding（`enable_rag=False`）。
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SEND_GATE = int(os.environ.get("REACT_SEND_GATE", "14"))
MAX_CNY = float(os.environ.get("REACT_MAX_CNY", "0.08"))
CAP_SEC = float(os.environ.get("REACT_CAP_SEC", "300"))
# 单发上限：本仓现证过「累计闸拦不住单发」（76 字输入烧出 ¥1.73），所以闸要三件齐全——发数、每发后再判、**单发封顶**。
# REACT 的 `_think` 只回一个小 JSON（ActionChoice），700 token 足够；真被截断就是坏回包，走兜底、拿不到形状，
# 那属于「没验成」，由 exit 1 那条老实话接住，不许记成验过。
REACT_MAX_TOKENS = int(os.environ.get("REACT_MAX_TOKENS", "700"))
PROJECT = "react_ac"
_sent = 0
_watch = {}          # 起跑后填：runner / bus / sid / 已花读数


class GateHit(RuntimeError):
    """撞闸专用异常：只死在这一个 asyncio 任务里，不许掀进程。"""


def _spent():
    cm = _watch["runner"].costs.get(_watch["sid"]) if _watch.get("runner") else None
    return float(getattr(cm, "cost_cny", 0.0) or 0.0)


def _hit_send_gate():
    """每发之后判三件事：发数、账上的钱、**形状到了没有**。撞任何一条都当场把这场停下。

    ⚠ 撞闸**不许 `sys.exit`**：这条钩子跑在 runner 的 asyncio 任务里，SystemExit 在线程里直接掀掉整个进程——
    09-30 21:0x 预检现证：读数一行没印、`TestClient` 的 portal 抛 `RuntimeError: This portal is not running`。
    而花钱件最要的正是「撞闸退出前必须把花费与中间读数取到手」。改成抛 `GateHit`：raise 在真调用**之前**
    （发已经拦住、不再出一分钱），主循环靠 `_watch["gate"]` 得知并先取读数再退。
    """
    global _sent
    _sent += 1
    why = None
    if _sent > SEND_GATE:
        why = f"撞发数闸（第 {_sent} 发 / 闸 {SEND_GATE}）"
    elif _spent() > MAX_CNY:
        why = f"撞金额闸（¥{_spent():.6f} > 闸 ¥{MAX_CNY}，第 {_sent} 发后）"
    elif _react_shape(_watch.get("bus"), _watch.get("sid"))[2] >= 1:
        # 停的条件是**定稿 content 到了**，不是「看见 meta」：meta 由 `on_chat_model_start` 在首 token 之前发，
        # 拿它当停条件会在 `on_chat_model_end` 之前掐掉这一发 ⇒ usage 不回填，④ 那本账就印成 0
        # （21:0x 现证过一次：账上 `total_prompt_tokens=0` 不是「没花钱」的证据）。
        print(f"   [形状已到] 第 {_sent} 发后已见 react 块的定稿 content ⇒ 当场 stop", flush=True)
        _stop_now("目标形状已到手（不必再往下跑）")
        return
    if why is None:
        print(f"   [真端点] 第 {_sent}/{SEND_GATE} 发｜已花 ¥{_spent():.6f}", flush=True)
        return
    print(f"🛑 撞闸：{why}——停手，抬闸要人拍", flush=True)
    _watch["gate"] = why
    _stop_now(why)
    raise GateHit(why)


def _stop_now(why: str):
    runner, sid = _watch.get("runner"), _watch.get("sid")
    if runner is not None and sid is not None:
        try:
            import asyncio
            asyncio.get_running_loop().create_task(runner.stop(sid))
        except Exception as e:                      # 停不掉就让墙钟兜，别让钩子把进程带崩
            print(f"   （就地 stop 没成功：{type(e).__name__} {e}——由墙钟兜底）", flush=True)
    print(f"   [stop] 原因：{why}", flush=True)


def _react_shape(bus, sid):
    """从**进程内 bus** 里找 react 块：返回 (meta 事件, 该 uuid 的逐片列表, 定稿 content 条数)。

    读的是 bus 的历史出口（与 SSE/回放同一条路），不是猜出来的事件形状——事件形状只从产品发出去的
    那份里取（这条是本仓钉过的规矩：形状不现证就不钉进判据）。
    """
    if bus is None or sid is None:
        return None, [], 0
    evs = bus.history(sid)
    meta = None
    for e in evs:
        v = getattr(e, "value", None)
        if (getattr(e, "name", "") == "meta" and isinstance(v, dict)
                and v.get("type") == "react" and list(v.get("prose_fields") or []) == ["thought"]):
            meta = e
            break
    if meta is None:
        return None, [], 0
    uid = getattr(meta, "uuid", None)
    live = [str(getattr(e, "value", "") or "") for e in evs
            if getattr(e, "name", "") == "live" and getattr(e, "uuid", None) == uid]
    fin = len([e for e in evs if getattr(e, "name", "") == "content"
               and getattr(e, "uuid", None) == uid])
    return meta, live, fin


IDENTITY_SHAPES = ("REACT", "BY_ORDER", "END")          # 枚举值：整片等于它才算漏（名单外）


def _install_send_gate():
    from openai.resources.chat.completions import AsyncCompletions, Completions
    orig_a, orig_s = AsyncCompletions.create, Completions.create

    async def gated_a(self, *a, **kw):
        _hit_send_gate()
        kw.setdefault("max_tokens", REACT_MAX_TOKENS)       # 单发封顶：累计闸拦不住「一发就跑完」
        return await orig_a(self, *a, **kw)

    def gated_s(self, *a, **kw):
        _hit_send_gate()
        kw.setdefault("max_tokens", REACT_MAX_TOKENS)
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
    _install_send_gate()

    from fastapi.testclient import TestClient
    from server.app import create_app

    tmp = Path(tempfile.mkdtemp())
    keep = (settings.enable_rag, settings.platform.auth_enabled, settings.platform.use_redis,
            ss.SESSIONS_FILE, settings.workspace_root)
    settings.enable_rag = False                 # 不掺向量腿，也不发 embedding
    settings.platform.auth_enabled = False
    settings.platform.use_redis = False         # 进程内 store/bus：不碰 db0、不 heal 别人的在跑会话
    ss.SESSIONS_FILE = tmp / "sessions.json"
    # 工作区**挪出仓库**：09-30 现证过两条线并发时 `finally` 里的 rmtree 会打到别人的在途产物，
    # 而 `server/settings.py:8-9` 在 import 时就把 `WORKSPACE_ROOT` 钉成仓内目录、runner 每次 `_prepare` 现读它。
    import server.settings as srv_settings
    settings.workspace_root = str(tmp / "ws")
    srv_settings.WORKSPACE_ROOT = tmp / "ws"
    (tmp / "ws").mkdir(parents=True, exist_ok=True)
    ws = tmp / "ws" / PROJECT
    t0 = time.time()
    sid = None
    try:
        with TestClient(create_app()) as c:
            r = c.post("/api/sessions", json={"idea": "写一个 20 行的猜数字小游戏，只用一个 html 文件",
                                              "project_name": PROJECT,
                                              "paradigm": "react",           # ← 这一条腿才有 REACT 档的 _think
                                              "permission": "readonly",      # ← 钉在「不写代码」那一档
                                              "n_round": 1})
            assert r.status_code == 200, f"建会话回 {r.status_code}: {r.text[:200]}"
            sid = r.json()["id"]
            app_runner = c.app.state.runner
            _watch.update(runner=app_runner, sid=sid, bus=c.app.state.bus)

            assert c.post(f"/api/sessions/{sid}/start").status_code == 200, "起跑失败"
            meta, live, fin = None, [], 0
            while time.time() - t0 < CAP_SEC:
                _watch["cost"] = c.get(f"/api/sessions/{sid}").json().get("cost") or {}
                meta, live, fin = _react_shape(_watch["bus"], sid)
                if meta is not None and fin >= 1:
                    break
                if _watch.get("gate"):
                    print("   主循环接住撞闸：" + _watch["gate"] + "——先把读数取到手再退", flush=True)
                    break
                st = c.get(f"/api/sessions/{sid}").json().get("status")
                if st in ("finished", "failed", "stopped"):
                    print(f"   这场在拿到 react 块之前就到了终态 status={st}", flush=True)
                    break
                time.sleep(1.5)
            _watch["cost"] = c.get(f"/api/sessions/{sid}").json().get("cost") or {}
            c.post(f"/api/sessions/{sid}/stop")
            time.sleep(1.0)
            cost = c.get(f"/api/sessions/{sid}").json().get("cost") or {}
            # 回放出口再取一次（与前端同一条读路，limit 上限是 1000，别写 2000 拿 422 咽成零事件）
            hist = c.get(f"/api/sessions/{sid}/events/history", params={"limit": 1000}).json().get("events") or []
            n_hist = len(hist)
            uid = getattr(meta, "uuid", None) if meta is not None else None
            pieces = [str(e.get("value") or "") for e in hist
                      if e.get("name") == "live" and e.get("uuid") == uid] if uid else []

        print(f"\n读数：出网 {_sent} 发｜墙钟 {int(time.time() - t0)}s｜回放出口 {n_hist} 条事件")
        if meta is None:
            print("exit 1：**没走到** ActionChoice 那一路（没有带 prose_fields=[thought] 的 react meta）"
                  f"——这场没跑到 Agent 的 REACT 档 _think。已花见上面，别把「没跑成」记成「验过了」")
            return 1
        joined = "".join(live)
        bare = [p for p in pieces if p.strip() in IDENTITY_SHAPES]          # 整片是个枚举值 ⇒ 名单外漏上屏
        bare_path = [p for p in pieces if p.strip().endswith((".py", ".js", ".jsx", ".html", ".md"))]
        print(f"① meta 带过 wire：uuid={uid} 的 value={getattr(meta, 'value', None)}")
        print(f"② 该块逐片 {len(pieces)} 条 / {len(joined)} 字｜整片是枚举值的 {len(bare)} 条"
              f"｜整片是裸文件名的 {len(bare_path)} 条")
        print(f"   逐片开头 60 字：{joined[:60]!r}")
        print(f"③ 定稿 content 条数：{fin}（live 与 content 是两条通道）")
        print(f"④ 产品那本账：{cost}")
        # 撞闸那一路要先退：中间产物已经印完，此时两条 assert 测的是「没跑完的形状」，让它们说了不算
        if _watch.get("gate"):
            print("\n⚠ 撞闸退出、**未跑完**：" + _watch["gate"] + "｜上面四格是中间产物，不是闭合读数")
            return 3
        assert bare == [] and bare_path == [], \
            f"名单外的东西成片上了屏（枚举值 {bare} / 裸文件名 {bare_path}）⇒ 门控在真模型上没挡住"
        assert len(joined) > 0, "②该块零逐片 ⇒ 名单挂上了但那一笔根本没流出来（这条读数不算活体走过）"
        print("\n✅ 欠账③ 闭合：ActionChoice 的名单在真模型真会话里过了 wire，逐片只出 thought")
        return 0
    finally:
        settings.enable_rag, settings.platform.auth_enabled, settings.platform.use_redis, \
            ss.SESSIONS_FILE, settings.workspace_root = keep
        import shutil as sh
        sh.rmtree(tmp, ignore_errors=True)
        print(f"清场：临时工作区存在={(tmp / 'ws').exists()}｜临时 sessions 存在={(tmp / 'sessions.json').exists()}"
              f"｜出网合计 {_sent} 发")


if __name__ == "__main__":
    sys.exit(main())
