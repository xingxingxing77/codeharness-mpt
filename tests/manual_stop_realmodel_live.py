"""C172 的真模型活体档（要重跑得先有新的预算授权——10-05 这一发已超支，见下面第二条）。

要验的那一步：产线那层的 cancel 形状在**真模型**下与本机桩同形——真发逐片、真点停止、
事件流里不许留任何还开着的块。前三层证据已在（翻译层 t16 五格 + 变异、产线层本机桩
`manual_stop_stream_close.py`、渲染层 shot.mjs 四帧对照），差的就是「模型真在吐字时被掐」。

10-05 02:4x 那一发**没达到目的还超支**（授权 ≤¥0.2、实花 ¥0.356093、`saw_live=false`：
react/readonly 的 `_think` 走 structured 压根没吐逐片）。所以本档现在带 **C176 的四条护栏**：
  · **前置闸**（`cost_gate.precheck`）：同形状历史最贵一场 > 闸 或 没有历史 ⇒ **不发**；
  · **撞线判定挂在记账那一刻**（`cost_gate.armed`）：不再靠轮询读快照——轮询永远慢一整发；
  · **单发封顶**：`settings.llm.timeout`（默认 45s）是唯一掐得住「在飞那一发」的东西
    （`max_token` 对 thinking 模型不封顶）。⚠ 超时那一发账上是零（B6 `_timeout_lost`），
    所以它只压规模、不代替前两条；
  · 发数上限 + 墙钟上限 + 读数在清场前取（账本在 `_forget(terminal=True)` 里被 pop，
    散会之后再读就是 ¥0.000000——闸自己留了一份 `state`）。
撞闸只 raise 不 sys.exit（掀了进程就读不到数），读数一律落盘。

跑法（零花费的管路预检把端点指到丢弃端口 9，并把闸抬到过估值之上，好让前置闸放行）：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
  PYTHONDONTWRITEBYTECODE=1 REDIS__DB=15 LANGFUSE__ENABLED=0 NO_PROXY=127.0.0.1,localhost,::1 \\
  PREFLIGHT=dead CAP_CNY=0.40 F:/anaconda/python.exe -B tests/manual_stop_realmodel_live.py
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
N_CALLS_CAP = int(os.environ.get("N_CALLS_CAP", "8"))      # 记账笔数上限（一笔一发）
MAX_TOKEN = 600         # 名义单发上限——⚠ thinking 模型不认它，别当花费上限读
CALL_DEADLINE_SEC = int(os.environ.get("CALL_DEADLINE_SEC", "45"))   # 唯一掐得住在飞那一发的闸
WALL_SEC = int(os.environ.get("WALL_SEC", "150"))
PARADIGM = os.environ.get("PARADIGM", "react")   # 前置闸按这一档的历史单价估；10-05 用户拍「换 dynamic 线」
IDEA = os.environ.get("IDEA", "用两句话说清什么是二分查找，不要写文件、不要建计划")
OUT = Path(os.environ.get("OUT", "E:/tmp/c176/live_readings.json"))


def main() -> int:
    from codeharness.configs.settings import settings
    from server.runner import cost_snapshot        # 快照的唯一出口在 runner（GET 与 SSE 同形那份）
    from tests.cost_gate import CapHit, armed, precheck
    import server.settings as se
    import server.sessions as ss

    tmp = Path(tempfile.mkdtemp())
    se.WORKSPACE_ROOT = tmp / "workspace"
    (tmp / "workspace").mkdir(parents=True, exist_ok=True)
    ss.SESSIONS_FILE = tmp / "sessions.json"
    settings.platform.use_redis = False
    settings.enable_rag = False
    settings.llm.max_token = MAX_TOKEN
    settings.llm.timeout = CALL_DEADLINE_SEC      # C176：单发封顶走这里，不走 max_token
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

    # 前置闸：估不动 / 闸罩不住 / 币种对不上 ⇒ 一分钱都不发（10-05 那次超支的直接解）
    reason = precheck(PARADIGM, CAP_CNY, settings.llm.model)
    if reason:
        print(f"❌ 前置闸拦下，未发出任何请求：{reason}")
        return 2
    print(f"前置闸放行：形状={PARADIGM} 闸=¥{CAP_CNY} 单发墙钟={CALL_DEADLINE_SEC}s")

    from fastapi.testclient import TestClient
    from server.app import create_app

    readings = {"cap_cny": CAP_CNY, "n_calls_cap": N_CALLS_CAP, "max_token": MAX_TOKEN,
                "call_deadline_sec": CALL_DEADLINE_SEC}
    hit = ""
    uninstall, gate = armed(CAP_CNY)
    with TestClient(create_app()) as c:
        try:
            sid = c.post("/api/sessions", json={"idea": IDEA,
                                                "project_name": "c176live", "paradigm": PARADIGM,
                                                "permission": "readonly", "n_round": 1}).json()["id"]
            runner = c.app.state.runner
            c.post(f"/api/sessions/{sid}/start")
            t0 = time.time()
            saw_live = False
            while time.time() - t0 < WALL_SEC:
                evs = c.get(f"/api/sessions/{sid}/events/history", params={"limit": 0}).json().get("events", [])
                if any(e.get("name") == "live" for e in evs):
                    saw_live = True
                cm = runner.costs.get(sid)
                if cm is not None:                   # 清场前现取：账本在 _forget 里会被 pop
                    readings["last_snapshot"] = cost_snapshot(cm)
                if gate["hit"] is not None:          # ← 撞线判定在记账那一刻，不在这里
                    hit = f"撞金额闸（第 {gate['hit'].calls} 笔记账）：{gate['hit']}"
                    break
                if gate["calls"] >= N_CALLS_CAP:
                    hit = f"撞发数闸（记账 {gate['calls']} 笔）"
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
            readings["gate_peak_cny"] = gate["peak_cny"]
            readings["gate_peak_usd"] = gate["peak_usd"]
            readings["gate_calls"] = gate["calls"]
            readings["cap_hit"] = (None if gate["hit"] is None else
                                   {"cny": gate["hit"].cny, "usd": gate["hit"].usd,
                                    "calls": gate["hit"].calls})
            # 无论撞没撞闸都走一次真 /stop：这一档测的就是「停止那一场没有悬挂块」
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
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps(readings, ensure_ascii=False, indent=1), encoding="utf-8")
        except CapHit as e:
            # 闸从图里抛穿到这里＝「下一发发不出去」的正证：它绕过了 `except Exception` 那几层
            # 兜底重问（`role_zero._think` 的 aask 兜底、`agent.py` 的 C76 兜底都会咽掉普通异常）
            readings["escaped_cap_hit"] = {"cny": e.cny, "usd": e.usd, "calls": e.calls}
            readings["gate_calls"] = gate["calls"]
            readings["gate_peak_cny"] = gate["peak_cny"]
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps(readings, ensure_ascii=False, indent=1), encoding="utf-8")
            print(json.dumps(readings, ensure_ascii=False, indent=1))
            print(f"❌ 闸从图里抛穿（第 {e.calls} 笔记账 ¥{e.cny:.6f}）——这笔钱已花但没有下一发；"
                  f"会话态可能仍停在 running，隔离 store 随临时目录作废")
            return 1
        finally:
            uninstall()                                 # 泄漏的闸会掐掉同进程后面的任何会话

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
             f"峰值 ¥{gate['peak_cny']:.6f}（闸 ¥{CAP_CNY}）、{readings['wall_sec']}s")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
