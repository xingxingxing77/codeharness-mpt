"""C172 的真模型活体档（用户令「按推荐的做」＝授权这一发，硬闸 ≤¥0.2）。

要验的那一步：产线那层的 cancel 形状在**真模型**下与本机桩同形——真发逐片、真点停止、
事件流里不许留任何还开着的块。前三层证据已在（翻译层 t16 五格 + 变异、产线层本机桩
`manual_stop_stream_close.py`、渲染层 shot.mjs 四帧对照），差的就是「模型真在吐字时被掐」。

有界活体三条护栏（都是本仓踩过的，写在起跑进程内、不靠外部看）：
  · 单发封顶：`settings.llm.max_token` 压到 600（thinking 模型一发能烧 ¥1.73 的先例在册）；
  · 发数上限：数 trace 的 span 条数（一笔一条）＋在途一笔，超 N_CALLS_CAP 立刻停；
  · 每发后再判 + 轮询期现取花费：账本在 `_forget(terminal=True)` 里被 pop，
    散会之后再读就是 ¥0.000000（「账上 0 ≠ 没花钱」），所以每次轮询都从 `runner.costs[sid]` 现取。
撞闸只 raise 不 sys.exit（掀了进程就读不到数），读数一律落盘。
跑法：cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
      PYTHONDONTWRITEBYTECODE=1 REDIS__DB=15 LANGFUSE__ENABLED=0 NO_PROXY=127.0.0.1,localhost,::1 \\
      F:/anaconda/python.exe -B tests/manual_stop_realmodel_live.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "logs"))

CAP_CNY = float(os.environ.get("CAP_CNY", "0.20"))          # 用户授权的硬闸
N_CALLS_CAP = int(os.environ.get("N_CALLS_CAP", "8"))      # 数 end_marker 条数（一笔一条）
MAX_TOKEN = 600         # 单发封顶
WALL_SEC = int(os.environ.get("WALL_SEC", "150"))
OUT = Path("E:/tmp/c175/live_readings.json")


class GateHit(Exception):
    """撞闸：只中断流程，不掀进程（掀了就取不到花费读数）。"""


def main() -> int:
    from codeharness.configs.settings import settings
    from server.runner import cost_snapshot        # 快照的唯一出口在 runner（GET 与 SSE 同形那份）
    import server.settings as se
    import server.sessions as ss

    tmp = Path(tempfile.mkdtemp())
    se.WORKSPACE_ROOT = tmp / "workspace"
    (tmp / "workspace").mkdir(parents=True, exist_ok=True)
    ss.SESSIONS_FILE = tmp / "sessions.json"
    settings.platform.use_redis = False
    settings.enable_rag = False
    settings.llm.max_token = MAX_TOKEN
    settings.llm.stream = True
    if os.environ.get("PREFLIGHT") == "dead":
        # 零花费管路预检：端点指到丢弃端口（9），验「脚本能跑完并给出诚实判红」而一分钱不花
        settings.llm.base_url = "http://127.0.0.1:9/v1"
        settings.llm.api_key = "preflight"
    # 端点与凭据照 .env 原样用（不写 .env、不打印密钥）；只印长度与 sha8 供对账
    import hashlib
    key = settings.llm.api_key or ""
    print(f"端点={settings.llm.base_url} 模型={settings.llm.model} "
          f"key长度={len(key)} sha8={hashlib.sha256(key.encode()).hexdigest()[:8] if key else '无'}")

    from fastapi.testclient import TestClient
    from server.app import create_app

    readings = {"cap_cny": CAP_CNY, "n_calls_cap": N_CALLS_CAP, "max_token": MAX_TOKEN}
    hit = ""
    with TestClient(create_app()) as c:
        sid = c.post("/api/sessions", json={"idea": "用两句话说清什么是二分查找，不要写文件、不要建计划",
                                            "project_name": "c175live", "paradigm": "react",
                                            "permission": "readonly", "n_round": 1}).json()["id"]
        runner = c.app.state.runner
        c.post(f"/api/sessions/{sid}/start")
        t0 = time.time()
        saw_live = False
        peak = 0.0
        while time.time() - t0 < WALL_SEC:
            evs = c.get(f"/api/sessions/{sid}/events/history", params={"limit": 0}).json().get("events", [])
            if any(e.get("name") == "live" for e in evs):
                saw_live = True
            cm = runner.costs.get(sid)
            if cm is not None:                       # 清场前现取：账本在 _forget 里会被 pop
                snap = cost_snapshot(cm)
                peak = max(peak, float(snap.get("cost_cny") or 0))
                readings["last_snapshot"] = snap
            done = [e for e in evs if e.get("name") == "end_marker"]
            if len(done) >= N_CALLS_CAP:
                hit = f"撞发数闸（end_marker {len(done)} 条）"
                break
            if peak > CAP_CNY:
                hit = f"撞金额闸 ¥{peak:.6f} > ¥{CAP_CNY}"
                break
            if saw_live:
                break                                 # 目的达成：真模型正在吐字，此刻掐它
            st = c.get(f"/api/sessions/{sid}").json().get("status") or ""
            if st in ("finished", "stopped", "failed"):
                hit = f"没等到逐片，会话先落终态 {st}"    # 场子已经死了就别再等墙钟（预检现证会白等 150s）
                break
            time.sleep(0.2)
        readings["saw_live"] = saw_live
        readings["gate_hit"] = hit
        readings["peak_cny_before_stop"] = peak
        stop = c.post(f"/api/sessions/{sid}/stop")
        readings["stop_http"] = stop.status_code

        status = ""
        t1 = time.time()
        while time.time() - t1 < 30:
            status = c.get(f"/api/sessions/{sid}").json().get("status", "")
            if status in ("stopped", "finished", "failed"):
                break
            time.sleep(0.2)
        readings["final_status"] = status
        evs = c.get(f"/api/sessions/{sid}/events/history", params={"limit": 0}).json().get("events", [])
        reports = [(e.get("name"), e.get("uuid"), e.get("block")) for e in evs if e.get("kind") == "report"]
        last_of = {}
        for n, u, _b in reports:
            if u:
                last_of[u] = n
        readings["report_blocks"] = len(last_of)
        readings["events_total"] = len(reports)
        readings["dangling"] = {u: n for u, n in last_of.items() if n != "end_marker"}
        readings["blocks_detail"] = [{"uuid": u[:12], "last": n} for u, n in last_of.items()]
        readings["cost_after"] = c.get(f"/api/sessions/{sid}").json().get("cost")
        readings["wall_sec"] = round(time.time() - t0, 1)
        # 终态之后账本已被 _forget pop（这正是「清场前取数」那条坑的成因），留 GET 出口那份
        OUT.write_text(json.dumps(readings, ensure_ascii=False, indent=1), encoding="utf-8")

    fails = []
    if not saw_live:
        fails.append("① 前提没成立：真模型没吐出逐片就退场了，这格测不到「飞行中被掐」")
    if status not in ("stopped", "finished", "failed"):
        fails.append(f"② {WALL_SEC}s 内没落终态（现值 {status!r}）")
    if not last_of:
        fails.append("③ 事件流里一颗 report 块都没有 ⇒ 没在测收口")
    elif readings["dangling"]:
        fails.append(f"④ 真模型停止后仍有没收口的块：{readings['dangling']}")
    print(json.dumps(readings, ensure_ascii=False, indent=1))
    print("\n".join(f"❌ {f}" for f in fails)
          or f"✅ 真模型活体过：出字后停止 → {readings['report_blocks']} 颗块全部收口、"
             f"峰值 ¥{peak:.6f}（闸 ¥{CAP_CNY}）、{readings['wall_sec']}s")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
