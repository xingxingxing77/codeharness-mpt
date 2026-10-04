"""C164：经验池真场活体读数（用户 10-04 授权 ≤¥1；manual 通道，真钱，**不进门禁**）。

⚠ 第一条读数是探索期撞出来的**产品事实**（19:4x 三次试跑现证）：exp_cache 唯一接线在
RoleZero.llm_cached_think（role_zero.py:343），而 **classic 默认线走 classic_team=Agent**
（runner.py:566 → team.py:134 `from codeharness.roles.agent import Agent`）——默认线上
经验池**零接线**（三场 classic 真跑 ¥0.05：零命中行、零新经验行、连"读取失败"警告都没有、
Qdrant 默认集合始终不存在）。要量池必须跑 **dynamic**（RoleZero 线）。classic 要不要接线
另立待拍，不在本工装里顺手改。

回答的问题：EXP_POOL 四开关真开 + dynamic 真端点真会话，量 0.9 余弦阈值对真中文请求的——
  ① 命中行数与 sim 分布（"经验命中" INFO 行）；
  ② 复跑省钱：同题复跑场的 ¥ 对冷写场（dynamic 单发贵，场数压到 3）；
  ③ 误命中审计：sim>=0.9 但命中的是**别人**的经验（全员同 tag，跨角色跨步骤撞车是真实敞口；
     任务关键词互斥自动判 + 会话原文落盘供人复核）；
  ④ 改写召回：同义改写题按题面查池 top3；
  ⑤ 池点数（Qdrant doc_type=exp）与 db15 exp_hits 键数；C165 打分腿真钱读数（新经验数=打分发数）。

场次（3 场 dynamic，真 StepFun；dynamic 单发均 ¥0.078，C114 读数）：
  T1 冷写 → T1r 原题复跑 → P1 同义改写（≈T1）。T2/T3 砍掉：①③④ 的判别力不受影响，⑤ 的
  池点数由 T1 一场贡献（多角色多步的 CMD_PROMPT 各成一条）。

硬闸四件（SDK 最底层；撞闸只 raise 不掀进程）：
  EXP_SEND_GATE（14 发/场——dynamic 一场 17+ 发，C114）、EXP_MAX_CNY（¥0.30/场）、
  EXP_CAP_SEC（240s/场墙钟）、EXP_TOTAL_CNY（¥0.84 全局——超线不再开新场）。
  ⚠ 残余敞口照 C114 记账：这家端点 thinking 不受 max_tokens 约束，单发跑飞金额闸只能事后拦。

仪器三伤（第一版工装的，照实记）：
  ① `_sent = 0` 在 main 里赋成局部变量、闸函数改的是全局 ⇒ 闸跨场累积，第二场起每发必撞；
  ② 会话终态后 `_forget(terminal)` 清掉 runner.costs ⇒ 行读数要在轮询期现取峰值；
  ③ logs.py 的 setup 会 `logger.remove()`——工装的收集 sink 必须在 create_app **之后**再挂。

跑法（要 `.env` 那台 StepFun；Qdrant 要在线；embedding 用 `.env` 生产同款）：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 \\
    REDIS__DB=15 LANGFUSE__ENABLED=0 NO_PROXY=127.0.0.1,localhost,::1 F:/anaconda/python.exe -B tests/manual_exp_pool_live.py
"""
import os
import sys
import time
import json
from pathlib import Path

# EXP_POOL 四开关必须在 settings 导入前进环境（这是"真开池"的唯一姿势，不碰 .env）
os.environ.setdefault("EXP_POOL__ENABLED", "true")
os.environ.setdefault("EXP_POOL__ENABLE_READ", "true")
os.environ.setdefault("EXP_POOL__ENABLE_WRITE", "true")
os.environ.setdefault("EXP_POOL__ENABLE_SCORE", "true")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SEND_GATE = int(os.environ.get("EXP_SEND_GATE", "14"))
MAX_CNY = float(os.environ.get("EXP_MAX_CNY", "0.30"))
CAP_SEC = float(os.environ.get("EXP_CAP_SEC", "240"))
TOTAL_CNY = float(os.environ.get("EXP_TOTAL_CNY", "0.84"))

T1 = "写一个 Python 脚本把当前目录下所有 .txt 文件合并成一个 merged.txt，每行前面加上来源文件名"
P1 = "帮我用 Python 合并当前目录的全部 txt 文本到 merged.txt，并且每一行开头标注它来自哪个文件"
RUNS = [("T1", T1, 1), ("T1r", T1, 2), ("P1", P1, 2)]
MARKERS = {"T1": ["merged", "txt"]}
LOGS = []          # loguru 收集的格式化行（sink 在 create_app 之后再挂，见文件头仪器伤 ③）
_watch = {"sent": 0}


class GateHit(RuntimeError):
    pass


def _total_spent():
    runner = _watch.get("runner")
    if runner is None:
        return 0.0
    try:
        return sum(float(getattr(cm, "cost_cny", 0.0) or 0.0) for cm in runner.costs.values())
    except Exception:
        return 0.0


def _session_spent():
    runner, sid = _watch.get("runner"), _watch.get("sid")
    cm = runner.costs.get(sid) if (runner and sid) else None
    return float(getattr(cm, "cost_cny", 0.0) or 0.0)


def _hit_gate():
    """每发判三件事：本场发数、本场金额、全局金额。撞任何一条当场 stop（只死在当前场的任务里）。"""
    _watch["sent"] = _watch.get("sent", 0) + 1
    why = None
    if _watch["sent"] > SEND_GATE:
        why = f"撞发数闸（本场第 {_watch['sent']} 发 / 闸 {SEND_GATE}）"
    elif _session_spent() > MAX_CNY:
        why = f"撞金额闸（本场 ¥{_session_spent():.4f} > ¥{MAX_CNY}）"
    elif _total_spent() > TOTAL_CNY:
        why = f"撞全局金额闸（累计 ¥{_total_spent():.4f} > ¥{TOTAL_CNY}）"
    if why:
        print(f"   🛑 {why}——本场停手", flush=True)
        _watch["gate"] = why
        raise GateHit(why)
    print(f"   [真端点] 本场第 {_watch['sent']}/{SEND_GATE} 发｜本场 ¥{_session_spent():.4f}｜累计 ¥{_total_spent():.4f}", flush=True)


def _install_gate():
    from openai.resources.chat.completions import AsyncCompletions, Completions
    orig_a, orig_s = AsyncCompletions.create, Completions.create

    async def gated_a(self, *a, **kw):
        _hit_gate()
        kw.setdefault("max_tokens", 640)
        return await orig_a(self, *a, **kw)

    def gated_s(self, *a, **kw):
        _hit_gate()
        kw.setdefault("max_tokens", 640)
        return orig_s(self, *a, **kw)

    AsyncCompletions.create, Completions.create = gated_a, gated_s


def _bucket_log(cut):
    rows = LOGS[cut:]
    hits = [m for m in rows if "经验命中" in m]
    news = [m for m in rows if "New experience" in m]
    warns = [m for m in rows if "经验池读取失败" in m or "经验入库" in m or "经验打分失败" in m]
    return hits, news, warns


def _session_texts(bus, sid):
    """本场 bus 历史里所有带文本的事件正文拼串（误命中审计的料），截断防刷屏。"""
    try:
        chunks = []
        for e in bus.history(sid):
            for attr in ("content", "text", "value"):
                v = getattr(e, attr, None)
                if isinstance(v, str) and v.strip():
                    chunks.append(v)
        return "\n".join(chunks)[:6000]
    except Exception as e:
        return f"<bus 读取失败 {type(e).__name__}: {e}>"


def _misfire_audit(code, texts):
    """本场文本里**只有别题**关键词而没有本题关键词 ⇒ 可疑误命中（自动判，原文落盘供人复核）。"""
    origin = {"P1": "T1"}.get(code, code[:-1] if code.endswith("r") else code)
    own = MARKERS.get(origin)
    if own is None:
        return None
    low = texts.lower()
    if any(k in low for k in own):
        return None
    for other, keys in MARKERS.items():
        if other != origin and any(k in low for k in keys):
            return other
    return None


def _sims_of(hits):
    out = []
    for h in hits:
        try:
            out.append(float(h.split("sim=")[1].split("）")[0].split(")")[0]))
        except Exception:
            pass
    return out


def main() -> int:
    import tempfile
    import asyncio
    import httpx
    import server.sessions as ss
    from loguru import logger
    from codeharness.configs.settings import settings

    if not settings.llm.api_key:
        sys.exit("exit 1：.env 里没有 LLM 凭据，本工装不发空请求")
    if "stepfun" not in (settings.llm.base_url or ""):
        sys.exit(f"exit 1：LLM 端点不是 .env 那台（{settings.llm.base_url}）——替身读数没有资格")
    if not (settings.exp_pool.enabled and settings.exp_pool.enable_read
            and settings.exp_pool.enable_write and settings.exp_pool.enable_score):
        sys.exit("exit 1：EXP_POOL 四开关没全开——本工装量的就是开池行为")
    # 前置探活：embedding/Qdrant 不在线时经验池必然全灭，仪器坏的读数不发
    try:
        from codeharness.provider.gateway import LLMGateway
        v = asyncio.run(LLMGateway.embeddings().aembed_query("探活"))
        assert len(v) == settings.embedding.dim, f"embedding 维度不对 {len(v)}"
    except Exception as e:
        sys.exit(f"exit 1：embedding 不在线（{settings.embedding.base_url} / {settings.embedding.model}）：{type(e).__name__} {e}")
    try:
        assert httpx.get(f"{settings.qdrant.url}/healthz", timeout=5).status_code == 200
    except Exception as e:
        sys.exit(f"exit 1：Qdrant 不在线（{settings.qdrant.url}）：{e}")
    redis_note = ""
    try:
        import redis as sync_redis
        sync_redis.Redis(host=settings.redis.host, port=settings.redis.port,
                         db=settings.redis.db).ping()
    except Exception as e:
        redis_note = f"Redis(db{settings.redis.db}) 不在线：命中计数全 0（{type(e).__name__}）"
        print(f"⚠ {redis_note}")

    _install_gate()

    from fastapi.testclient import TestClient
    from server.app import create_app

    tmp = Path(tempfile.mkdtemp())
    keep = (settings.enable_rag, settings.platform.auth_enabled, settings.platform.use_redis,
            ss.SESSIONS_FILE, settings.workspace_root)
    settings.enable_rag = False            # 关 kb 腿：别让知识库召回的 embedding/检索搅进经验池读数
    settings.platform.auth_enabled = False
    settings.platform.use_redis = False    # 不占 db15 排他资源；HitCounter 自己连 db15、键带前缀不冲突
    ss.SESSIONS_FILE = tmp / "sessions.json"
    import server.settings as srv_settings
    settings.workspace_root = str(tmp / "ws")
    srv_settings.WORKSPACE_ROOT = tmp / "ws"
    (tmp / "ws").mkdir(parents=True, exist_ok=True)

    results = []
    total_cny = 0.0
    hid = None
    try:
        with TestClient(create_app()) as c:
            # logs.py 的 setup 在 create_app 里会 logger.remove()——收集 sink 只能这时候挂（仪器伤 ③）
            hid = logger.add(lambda line: LOGS.append(line), level="DEBUG", format="{message}")
            runner, bus = c.app.state.runner, c.app.state.bus
            for code, idea, rnd in RUNS:
                if _total_spent() > TOTAL_CNY:
                    print(f"🛑 全局 ¥{_total_spent():.4f} 已超线，{code} 弃跑", flush=True)
                    break
                cut, t0 = len(LOGS), time.time()
                r = c.post("/api/sessions", json={
                    "idea": idea, "project_name": f"c164_exp_{code}",
                    "paradigm": "dynamic", "permission": "workspace_write", "n_round": 3})
                assert r.status_code == 200, f"建会话回 {r.status_code}: {r.text[:200]}"
                sid = r.json()["id"]
                _watch.update(runner=runner, sid=sid, gate=None, sent=0)
                assert c.post(f"/api/sessions/{sid}/start").status_code == 200, "起跑失败"
                status, cost_peak, warned = "running", 0.0, []
                while time.time() - t0 < CAP_SEC:
                    cost_peak = max(cost_peak, _session_spent())
                    try:
                        status = c.get(f"/api/sessions/{sid}").json().get("status", "?")
                    except Exception:
                        status = "?"
                    if status in ("finished", "failed", "stopped", "awaiting_human"):
                        break
                    time.sleep(3)
                else:
                    _watch["gate"] = f"墙钟 {CAP_SEC}s 到（status={status}）"
                    try:
                        c.post(f"/api/sessions/{sid}/stop")
                    except Exception:
                        pass
                    for _ in range(10):     # 等真到终态，场与场之间不留僵尸任务搅乱日志桶
                        time.sleep(2)
                        try:
                            status = c.get(f"/api/sessions/{sid}").json().get("status", "?")
                        except Exception:
                            status = "?"
                        if status in ("finished", "failed", "stopped", "awaiting_human"):
                            break
                cost_peak = max(cost_peak, _session_spent())
                total_cny += cost_peak
                gate_here = _watch.get("gate")
                hits, news, warned = _bucket_log(cut)
                texts = _session_texts(bus, sid)
                results.append({
                    "code": code, "round": rnd, "sid": sid, "status": status,
                    "sec": round(time.time() - t0, 1), "cost_cny": round(cost_peak, 6),
                    "gate": gate_here, "hit_lines": len(hits), "sims": _sims_of(hits),
                    "new_exps": len(news), "pool_warn_lines": len(warned),
                    "warn_sample": warned[0][:160] if warned else "",
                    "misfire_suspect": _misfire_audit(code, texts),
                    "first_hit_line": hits[0] if hits else "",
                    "session_text_excerpt": texts[:400],
                })
                print(f"◀ {code}: status={status} {time.time()-t0:.0f}s ¥{cost_peak:.4f} 命中{len(hits)} "
                      f"sims={[round(s, 3) for s in _sims_of(hits)]} 新经验{len(news)} 池警告{len(warned)} "
                      f"{'⚠ 可疑误命中→' + results[-1]['misfire_suspect'] if results[-1]['misfire_suspect'] else ''}",
                      flush=True)

            # ── ④ 改写题按题面查池 top3；⑤ 池点数与命中计数键 ──
            from codeharness.document_store.exp_store import ExpStore
            store = ExpStore(user_id="default")
            pool_probe = {}
            for code, idea, rnd in RUNS:
                if rnd != 2:
                    continue
                try:
                    got = asyncio.run(store.search("RoleZero.llm_cached_think", idea, k=3))
                    pool_probe[code] = [{"req": (g["input"] or "")[:60], "sim": round(g["score"], 4),
                                         "quality": g.get("quality_score")} for g in got]
                except Exception as e:
                    pool_probe[code] = f"<查池失败 {type(e).__name__}: {e}>"
            import redis as sync_redis
            from codeharness.document_store.qdrant_store import QdrantStore
            qs = QdrantStore()
            try:
                flt = QdrantStore._filters("exp", "default")
                n_points = asyncio.run(qs.client.count(
                    collection_name=qs.collection, count_filter=flt, exact=True)).count
            except Exception as e:
                n_points = f"<计数失败 {type(e).__name__}: {e}>"
            try:
                n_hit_keys = sum(1 for _ in sync_redis.Redis(
                    host=settings.redis.host, port=settings.redis.port,
                    db=settings.redis.db).scan_iter("exp_hits:*"))
            except Exception as e:
                n_hit_keys = f"<扫描失败 {type(e).__name__}: {e}>"
    finally:
        if hid is not None:
            logger.remove(hid)
        # 清场：池点与计数键都收走（读数已先落盘；池回到"从未启用"的干净态）
        try:
            from codeharness.document_store.exp_store import ExpStore
            asyncio.run(ExpStore(user_id="default").store.delete_scope(doc_type="exp", user_id="default"))
            import redis as sync_redis
            rc = sync_redis.Redis(host=settings.redis.host, port=settings.redis.port, db=settings.redis.db)
            keys = list(rc.scan_iter("exp_hits:*"))
            if keys:
                rc.delete(*keys)
            print(f"清场：exp 点与 exp_hits 键（{len(keys)} 支）已收走", flush=True)
        except Exception as e:
            print(f"清场失败（读数已落盘，不受影响）：{type(e).__name__}: {e}", flush=True)
        (settings.enable_rag, settings.platform.auth_enabled, settings.platform.use_redis,
         ss.SESSIONS_FILE, settings.workspace_root) = keep
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    # ── 汇总读数 ──
    cold = next((x for x in results if x["round"] == 1), None)
    rerun = next((x for x in results if x["code"] == "T1r"), None)
    para = next((x for x in results if x["code"] == "P1"), None)
    print("\n════ C164 读数汇总 ════")
    print(f"① 命中：全场命中行 {sum(x['hit_lines'] for x in results)}；"
          f"各场 sims={[x['sims'] for x in results]}")
    if cold and rerun:
        print(f"② 省钱：冷写 ¥{cold['cost_cny']}（{cold['status']}）→ 复跑 ¥{rerun['cost_cny']}"
              f"（{rerun['status']}，命中 {rerun['hit_lines']} 行）⇒ 差 ¥{round(cold['cost_cny'] - rerun['cost_cny'], 4)}")
    mis = [(x["code"], x["misfire_suspect"]) for x in results if x["misfire_suspect"]]
    print(f"③ 误命中可疑场：{mis if mis else '无'}（原文摘录见 JSON）")
    print(f"④ 改写题查池 top3：{json.dumps(pool_probe, ensure_ascii=False)[:600]}")
    print(f"⑤ 池点数（exp/default）：{n_points}｜exp_hits 键数：{n_hit_keys}｜"
          f"新经验（=打分发数）{sum(x['new_exps'] for x in results)}｜池警告 {sum(x['pool_warn_lines'] for x in results)} 行")
    print(f"账：全场累计 ¥{total_cny:.4f}（闸线 ¥{TOTAL_CNY}，授权 ≤¥1）｜{redis_note}")
    out = ROOT / "storage" / "benchmark"
    out.mkdir(parents=True, exist_ok=True)
    (out / "c164_exp_pool_live.json").write_text(json.dumps(
        {"note": "C164 经验池真场活体读数（10-04，StepFun+生产同款 embedding，EXP_POOL 四开，"
                 "dynamic 3 场：T1 冷写 + T1r 原题复跑 + P1 同义改写；classic 线零接线见文件头）",
         "results": results, "pool_probe": pool_probe,
         "pool_points": n_points, "hit_keys": n_hit_keys,
         "total_cny": round(total_cny, 6)}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"读数落盘 {out / 'c164_exp_pool_live.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
