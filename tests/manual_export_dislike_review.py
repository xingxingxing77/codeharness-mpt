"""C183：负反馈会话 → 复盘清单的导出口（评估闭环的人半边；manual 通道，零花费）。

回答的问题：轮尾 dislike 票（B4）落进 `session.feedback` 之后**只活在当前浏览器的用量页里**——
关掉浏览器、换台机器，坏会话就沉底了； Langfuse 侧更是一点痕迹没有。本工装把「带 dislike 票的
会话」枚举成一份落盘的复盘清单（`storage/benchmark/dislike_review.json`），它是后续一切优化的
原料：喂给人工复盘、或将来灌进 Langfuse dataset 做回归。

**形状改判一次（§4.3，10-06 现场取）**：登记时的候选①「票时在 Langfuse 记 score」**不做**——
v4 的 score 要挂 trace_id 而后端手里没有（handler 在进程内自生成，没人回填 session 记录），
造一条空 trace 挂分是假数据；候选②落成**文件清单**而不是 Langfuse dataset——dislike 会话是
给人复盘的，不是 input→expected_output 的回归对；且 langfuse web 容器与 mall-minio 的端口
冲突未解（C181 未验①），对真服务的写入没法现场验。文件清单今天就能真验（db0 只读，授权在 §3）。

零花费、**只读**：`RedisSessionStore(config=RedisConfig(db=0))` 只调 `list()`（zrevrange+hgetall），
不 create/update/heal——写库的出口一个不碰；`REDIS__DB` 环境变量在这里**故意不生效**（写死 db=0：
复盘清单要的就是 dev 真会话，指到沙盒 15 是空跑）。

跑法：
  自检（零服务、常跑）：
    cd /e/Codeharness && PYTHONPATH=/e/Codeharness PYTHONIOENCODING=utf-8 \
      F:/anaconda/python.exe -B tests/manual_export_dislike_review.py
  live（只读 db0，落盘清单）：
    同上 + --live
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "storage" / "benchmark" / "dislike_review.json"


def votes_of(feedback: dict) -> list[dict]:
    """feedback dict → 票表。两种形态照 `utils/votes.ts` 的归一口径：
    裸字符串 = 旧票（有票无时刻，at 给 ""——**是事实缺失，不是 0**，不拿 created_at 顶）。"""
    out = []
    for key, entry in (feedback or {}).items():
        if isinstance(entry, str):
            out.append({"key": key, "v": entry, "at": ""})
        else:
            out.append({"key": key, "v": (entry or {}).get("v", ""), "at": (entry or {}).get("at", "")})
    return out


def to_row(s) -> dict | None:
    """Session → 复盘行。没有 dislike 票的会话给 None（不进清单）。唯一整形口：自检与 live 同走。"""
    votes = [v for v in votes_of(s.feedback) if v["v"] == "dislike"]
    if not votes:
        return None
    cost = s.cost or {}
    return {
        "id": s.id,
        "idea": s.idea,
        "project": s.project_name,
        "paradigm": s.paradigm,
        "user_id": s.user_id,
        "status": s.status.value if hasattr(s.status, "value") else str(s.status),
        "error": s.error,
        "cost_cny": cost.get("cost_cny", ""),   # 取不到给空不给 0：账差要可见（同 N4 前端口径）
        "finished_at": s.finished_at,
        "dislikes": len(votes),
        "likes": sum(1 for v in votes_of(s.feedback) if v["v"] == "like"),
        "first_dislike_at": min((v["at"] for v in votes if v["at"]), default=""),
        "dislike_keys": [v["key"] for v in votes],
    }


def self_check() -> None:
    from types import SimpleNamespace as NS

    def sess(**kw):
        base = dict(id="s1", idea="做个网站", project_name="p", paradigm="classic", user_id="u",
                    status=NS(value="finished"), error="", cost={"cost_cny": 0.12},
                    finished_at="2026-10-06 10:00:00")
        return NS(**{**base, **kw})

    # 正常：1 dislike 1 like → 行带全字段
    r = to_row(sess(feedback={"t:a": {"v": "dislike", "at": "2026-10-06 09:59:00"},
                              "t:b": {"v": "like", "at": "2026-10-06 09:58:00"}}))
    assert r and r["dislikes"] == 1 and r["likes"] == 1 and r["cost_cny"] == 0.12
    assert r["first_dislike_at"] == "2026-10-06 09:59:00" and r["dislike_keys"] == ["t:a"]
    # 无票 / 只有 like ⇒ None
    assert to_row(sess(feedback={})) is None
    assert to_row(sess(feedback={"t:a": {"v": "like", "at": "x"}})) is None
    # 旧票形态（裸字符串）计数照算、at 事实缺失给空串
    r2 = to_row(sess(feedback={"t:a": "dislike"}, cost={}))
    assert r2["dislikes"] == 1 and r2["first_dislike_at"] == "" and r2["cost_cny"] == ""
    print("self_check: 4 格全绿（正常行 / 无票排除 / like 排除 / 旧票与缺账不造假 0）")


def live() -> None:
    from codeharness.configs.settings import RedisConfig
    from platforms.session_store import RedisSessionStore

    store = RedisSessionStore(config=RedisConfig(db=0))    # 只读 db0：复盘要的是 dev 真会话
    rows = []
    for s in store.list():                                 # list() = zrevrange + hgetall，零写出口
        try:
            row = to_row(s)
        except Exception as e:                            # 一条脏数据不陪葬整趟
            print(f"live: 跳过脏会话 {getattr(s, 'id', '?')}: {type(e).__name__}: {e}")
            continue
        if row:
            rows.append(row)
    rows.sort(key=lambda r: (-r["dislikes"], r["finished_at"] or ""))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"generated_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
                               "count": len(rows), "rows": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"live: {len(rows)} 个 dislike 会话 → {OUT}")
    for r in rows[:10]:
        idea = r["idea"][:24] + ("…" if len(r["idea"]) > 24 else "")
        print(f"  {r['id'][:8]}  d{r['dislikes']}/l{r['likes']}  {r['status']:<8} "
              f"¥{r['cost_cny']}  {r['finished_at'] or '（未收口）'}  {idea}")
    if not rows:
        print("live: 现值 0 条（还没有 dislike 票）——文件照落，账面为真")


if __name__ == "__main__":
    self_check()
    if "--live" in sys.argv:
        live()
