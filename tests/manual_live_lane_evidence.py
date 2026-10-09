"""C191（10-10）真模型活体工装——用户点头的那「几分钱」就是花在这里。

要回答的四件事（全都不能靠替身与桩，账写在 `plan/frontend.md` §1.6 的未验边界里）：
  ① 思考型模型那 40~51 秒静默期里，**车道停在哪一格**：相位文本、有没有在跑态、
     与「最后一个事件到现在的间隔」一起看——静默期界面上像不像卡死；
  ② `ms` 的**真实位数**（替身场是 3539/10/5ms，真模型下数量级完全不同，格式能不能受住）；
  ③ 刷新之后用户气泡还在不在（C189 新接的生产者，替身档只证到 HTTP 层）；
  ④ 点角色看整趟（这一条交给 shot.mjs，见本文件末尾打印的跑法）。

姿势照 §6 铁律 8：隔离实例（备用端口）+ `REDIS__DB=15` + `LANGFUSE__ENABLED=0`，
**不碰 8718、不改 `.env`、不动 db0**。事件是从真 SSE 连接上原样录下来的（客户端视角，
不是从 bus 内部读），所以「发了但连不上」这类洞也在这条路上。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 REDIS__DB=15 LANGFUSE__ENABLED=0 NO_PROXY=127.0.0.1,localhost,::1 \\
    F:/anaconda/python.exe -u -B tests/manual_live_lane_evidence.py --port 8795
跑完它会**继续挂着服务**（给 shot.mjs 量 ③④ 用），量完 Ctrl-C 或按 PID 停。
"""
import argparse
import json
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

OUT = Path("E:/tmp/live-c191")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8795)
    ap.add_argument("--idea", default="写一个 40 行以内的静态网页，用一段话讲清二分查找为什么每次能砍掉一半候选。"
                                        "不要测试、不要部署、不要外部依赖。")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--project", default="live_c191b")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    import httpx
    import uvicorn

    from server.app import create_app
    app = create_app()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    base = f"http://127.0.0.1:{args.port}"
    for _ in range(40):
        try:
            if httpx.get(base + "/api/health", timeout=2).status_code == 200:
                break
        except Exception:
            time.sleep(0.5)
    else:
        print("[红] 实例没起来")
        return 1

    b = httpx.Client(base_url=base, timeout=30)
    # `permission=workspace_write`：**默认的 readonly 档每写必批**，第一场就是这么停在
    # `awaiting_human` 的（现证：`approval:requested{tool:PrepareDocuments,
    # tier_required:workspace_write,tier_session:readonly}` ⇒ 状态 running→awaiting_human，
    # 车道收到的是 `completed{ms:46,aborted:true}`）。停着等人点的场次量不出真实静默期，
    # 所以这一场把档提到「工作区内写免审」。仍要批的（跑命令/联网 = full_access）在下面自动回一次。
    sid = b.post("/api/sessions", json={"idea": args.idea, "project_name": args.project,
                                        "paradigm": "classic", "n_round": args.rounds,
                                        "permission": "workspace_write"}).json()["id"]
    print(f"[起] 会话 {sid} · classic n_round={args.rounds} · permission=workspace_write · {base}")

    events = []
    sse_err = [""]

    def sse():
        # 真 EventSource 的等价物：同一条路由、同一个 after 口径，原样落盘（客户端视角）。
        # 连接自己断了要留名字，否则「一条都没录到」分不清是没发还是没连上。
        try:
            with httpx.stream("GET", f"{base}/api/sessions/{sid}/events?after=", timeout=None) as r:
                buf = ""
                for chunk in r.iter_text():
                    buf += chunk
                    while "\n\n" in buf:
                        frame, buf = buf.split("\n\n", 1)
                        for line in frame.splitlines():
                            if line.startswith("data: "):
                                try:
                                    ev = json.loads(line[6:])
                                except json.JSONDecodeError:
                                    continue
                                ev["_at"] = time.time()
                                events.append(ev)
        except Exception as e:                                  # noqa: BLE001 - 取证工装，什么错都要报出来
            sse_err[0] = f"{type(e).__name__}: {e}"

    th = threading.Thread(target=sse, daemon=True)
    th.start()
    t_start = time.time()
    r0 = b.post(f"/api/sessions/{sid}/start")
    if r0.status_code != 200:
        print(f"[红] /start {r0.status_code}：{r0.text[:200]}（没开跑，钱一分没花）")
        return 1

    status = ""
    asked = False
    parked = []
    while time.time() - t_start < 600:
        body = b.get(f"/api/sessions/{sid}").json()
        status = body.get("status", "")
        # ③ 用户句入流：**每 tick 试一次直到投进去**。第一场按「跑起来 25 秒后」定时机投，结果那
        # 25 秒里会话已经停在待批（`/chat` 在非 running 时是 409），一句也没进去。
        if not asked and status == "running":
            r = b.post(f"/api/sessions/{sid}/chat", json={"content": "只用一个 HTML 文件，别引入外部依赖。"})
            if r.status_code == 200:
                asked = True
                print(f"[③ /chat] 200 · 开跑后 {round(time.time() - t_start, 1)}s · 这句要在刷新后仍然看得见")
        # 停在待批：只替人点「允许一次」的那些**不会跑命令/不会出网**的（工作区内写）。
        # full_access 的要真问人，不代拍 ⇒ 直接收场并把 aid 打出来。
        if status == "awaiting_human":
            for it in b.get(f"/api/sessions/{sid}/approvals").json().get("pending", []):
                if it.get("tier_required") == "full_access":
                    parked.append(it)
                    print(f"[待拍] {it.get('tool')} 要 full_access，不代批 ⇒ 收场")
                    break
                rr = b.post(f"/api/sessions/{sid}/approvals/{it['id']}/respond",
                            json={"outcome": "allowed-once"})
                print(f"[批] {it.get('tool')}（要 {it.get('tier_required')} 档）→ {rr.status_code}")
            if parked:
                break
        if status in ("finished", "failed", "stopped"):
            break
        time.sleep(1)
    t_end = time.time()

    # 终局那几条可能还在 SSE 路上：等一秒取快照，否则「最后一条 → 收场」那段间隔是假的。
    time.sleep(1.0)
    live = list(events)

    # ---- 车道读数：从**录下来的事件**现算（不读 bus 内部），按角色分组 ----
    by_role: dict = defaultdict(list)
    for e in live:
        if e.get("kind") == "role":
            v = e.get("value") or {}
            by_role[v.get("role") or e.get("role")].append(e)

    # ①「静默期有多长、那段时间界面上写着什么」——**事后**从录下来的到达时刻算，不在轮询里猜：
    # 间隔 = 相邻两条事件之差，含「开跑→第一条」与「最后一条→收场」两头。
    # 只有 ping 心跳的时段没有 data 帧、不进这份表，那正是「什么都没说」的那段，算进间隔。
    marks = [t_start] + [e["_at"] for e in live] + [t_end]
    gaps = sorted(((marks[i + 1] - marks[i], marks[i], marks[i + 1]) for i in range(len(marks) - 1)),
                  reverse=True)
    quiet, g_from, g_to = (gaps[0] if gaps else (0.0, t_start, t_end))

    def open_at(ts):
        """那段时间**用户看得见的那一行**：还开着的车道（带最后相位与已停多久），
        以及停在等人那一档的车道（C193 之后那种收尾是 `paused`，行不亮但字写着「等你批准」）。"""
        rows = []
        for role, evs in by_role.items():
            seen = [e for e in evs if e["_at"] <= ts]
            if not seen:
                continue
            last = seen[-1]
            opened = sum(1 for e in seen if e["name"] == "started")
            closed = sum(1 for e in seen if e["name"] in ("completed", "paused"))
            if opened > closed:
                ph = [e for e in seen if e["name"] in ("started", "phase")][-1]
                rows.append(f"{role}={(ph.get('value') or {}).get('phase')}"
                            f"（已停 {round(ts - ph['_at'], 1)}s）")
            elif last["name"] == "paused":
                rows.append(f"{role} 停下等人（park={ (last.get('value') or {}).get('park') }，"
                            f"已等 {round(ts - last['_at'], 1)}s）")
        return rows

    kinds = Counter(f"{e.get('kind')}:{e.get('name')}" for e in live)
    users_live = [e for e in live if e.get("block") == "User" and e.get("name") == "content"]
    # ③ 的刷新档：跑完再按**首屏口径**拉一次历史（与前端 `loadFirstPage` 同一条路由、同一个 limit），
    # 用户句在里面 = 刷新后还看得见；不在 = 生产者其实没上流（HTTP 层 s17 t20 已证发，这里证留得住）。
    hist = b.get(f"/api/sessions/{sid}/events/history", params={"limit": 400}).json().get("events", [])
    users_hist = [e for e in hist if e.get("block") == "User" and e.get("name") == "content"]
    cost = b.get(f"/api/sessions/{sid}").json().get("cost") or {}

    (OUT / "events.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in live),
                                        encoding="utf-8")
    # 顺手把服务端那份也落盘（ts 是发布时刻、客户端 `_at` 是到达时刻，两个口径都要留：
    # 万一这场被 600s 上限截在中间，历史档还能接着算间隔）。
    (OUT / "history.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in hist),
                                        encoding="utf-8")
    print(f"\n[收场] status={status} · 墙钟 {round(t_end - t_start, 1)}s · 录到 {len(live)} 条事件"
          f"（SSE 线程 alive={th.is_alive()} err={sse_err[0] or '无'}）")
    print(f"[kind 直方图] {dict(kinds)}")
    print(f"[① 静默] 最长「没有事件的间隔」= {round(quiet, 1)}s（发生在开跑后 "
          f"{round(g_from - t_start, 1)}s ~ {round(g_to - t_start, 1)}s）")
    print(f"[① 静默] 那段时间开着的车道与当时的相位 = {open_at(g_from) or '无（没有车道开着）'}")
    print(f"[① 静默] 前三段间隔 = {[round(g, 1) for g, _a, _b in gaps[:3]]}s")
    for role, evs in by_role.items():
        ms = [int((e.get("value") or {}).get("ms") or 0) for e in evs if e["name"] == "completed"]
        trail = " → ".join(f"{round(e['_at'] - t_start)}s:{(e.get('value') or {}).get('phase')}"
                           for e in evs if e["name"] in ("started", "phase"))[:220]
        ab = sum(1 for e in evs if e["name"] == "completed" and (e.get("value") or {}).get("aborted"))
        pa = sum(1 for e in evs if e["name"] == "paused")     # C193：停在等人的那一档
        print(f"[② 车道] {role}: 开 {sum(1 for e in evs if e['name'] == 'started')} / "
              f"收 {len(ms)}（aborted {ab} / paused {pa}）· ms={ms} · 轨迹 {trail}")
    print(f"[③ User 块] 活流 {len(users_live)} 条 · 跑完重拉首屏 {len(users_hist)} 条 "
          f"⇒ 刷新后看得见 = {bool(users_hist) and len(users_hist) == len(users_live)}")
    print(f"[花费] cost={json.dumps(cost, ensure_ascii=False)}")
    print(f"[落盘] {OUT / 'events.jsonl'}")
    print(f"\n③④ 的 DOM 档接着量（实例还挂着，Ctrl-C 之前跑）：\n"
          f"  node frontend/scripts/shot.mjs --width 1286 --height 1000 --url {base}/?v=c191a "
          f"--eval-file E:/tmp/live-c191/eval_live_dom.js")
    srv.should_exit = False
    while True:
        time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
