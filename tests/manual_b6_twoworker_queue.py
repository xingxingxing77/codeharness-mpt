"""A⑤（B6②）多 worker 真两进程的插话队列竞态读数。

今天覆盖这条链的是 `s7 t4`：同一个进程里**两个线程**共用一台 RedisChatQueue——它证的是语义
（FIFO/幂等/不重复投递），证不了**两个进程**各自的进程内兜底 `_local` 会不会互相看不见。
本工装起真两个子进程（同一台 Redis db15、同一个队列键），一边持续 `drain`、一边并发 `remove`+`add`，
断言三件事：
  ① **同一件不许有两种结局**：被某个 worker 取走过的项，我们的 `remove` 必须回 `False`；
     回 `True` 就说明两个进程各自记各自的账（同一句插话会被用两次，或反过来以为自己撤掉了其实没撤）。
     另外「撤回被拒」的每条都必须**可归因**（在别人的收获里，或在我们更早一次成功撤回里）；
  ② 没有重复投递：两个 drain 方收到的项**互不相交**（Redis 那台靠逐条 LPOP 原子弹出，
     若哪天退回 LRANGE+DEL，这条就是唯一能露馅的地方）；
  ③ 没有丢件：投出去的每一条，最终要么被某一方取走、要么被我们撤回成功，**不能凭空消失**
     （C7 那件整件的由来就是「这条通道丢过一次消息」）。

**阳性对照 `--control`**：给 worker 注一个「撒谎的 remove」（不删也回 True）同法再跑一轮 ⇒ ① 必须红。
反证类判据自己不红一次，就不知道自己是有牙的还是恒真的（这条纪律的来历见台账）。
跑完自己清队列键与子进程（`REDIS__DB=15`，绝不碰 db0）。真服务缺席直接 exit 1 说「没跑成」。

跑法：
  cd /e/Codeharness && PYTHONPATH=/e/Codeharness:/e/Codeharness/logs PYTHONIOENCODING=utf-8 \\
    PYTHONDONTWRITEBYTECODE=1 REDIS__DB=15 LANGFUSE__ENABLED=0 NO_PROXY=127.0.0.1,localhost,::1 \\
    F:/anaconda/python.exe -B tests/manual_b6_twoworker_queue.py            # 真读数（必须绿）
    ... 同一行尾加 --control                                               # 对照（必须红在①）
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

WORKER = r"""
import json, os, sys, time
sys.path.insert(0, r"E:\Codeharness")
from platforms.chat_queue import RedisChatQueue

role, sid, budget_ms = sys.argv[1], sys.argv[2], int(sys.argv[3])
got = []                                     # 靠 on_change 拿 id：drain() 本身只回 (content, send_to)
q = RedisChatQueue(sid, on_change=lambda a, it: a == "drain" and got.extend(x["id"] for x in it))
if os.environ.get("B6_LIE_REMOVE") == "1":   # 阳性对照用：让 remove 撒谎（不删也说成功）
    q.remove = lambda qid: True
added, removed = [], []
t0 = time.time()
if role == "drain":
    while time.time() - t0 < budget_ms / 1000.0:
        q.drain()
        time.sleep(0.005)
else:
    i = 0
    while time.time() - t0 < budget_ms / 1000.0:
        if i % 3 == 2 and added:
            # 一半撤最新的（多半还在队里，应成功）、一半撤老的（多半已被某个 worker 取走，应被拒）——
            # 两种结局都是对的，被断言的是**同一件不许有两种结局**。
            victim = added[-1] if i % 6 == 2 else added[i % max(len(added) // 2, 1)]
            removed.append((victim, bool(q.remove(victim))))
        else:
            added.append(q.enqueue(f"投件{i}", "PM"))
        i += 1
    for it in q.pending():                   # 收尾：还留在队里的都撤掉，供母进程逐条对账
        removed.append((it["id"], bool(q.remove(it["id"]))))
print(json.dumps({"role": role, "got": got, "removed": removed, "added": added}))
"""


def main() -> int:
    import subprocess
    from codeharness.configs.settings import settings

    if settings.redis.db == 0:
        sys.exit("exit 1：本工装只许跑在隔离库（REDIS__DB=15），db0 是共享 dev 资源")
    try:
        import redis
        r = redis.Redis.from_url(settings.redis.to_url(), decode_responses=True)
        r.ping()
    except Exception as e:
        sys.exit(f"exit 1：Redis 不在线（{settings.redis.to_url()}）：{type(e).__name__}: {e}")

    from platforms.chat_queue import RedisChatQueue
    sid = f"s8b6twoproc{int(time.time())}"          # RedisChatQueue 自己拼键名（ch:chat:{sid}），别手拼第二个形状
    control = "--control" in sys.argv            # 阳性对照档：让 remove 撒谎，看①红不红
    env = {**os.environ, "PYTHONPATH": r"E:\Codeharness;E:\Codeharness\logs",
           "PYTHONIOENCODING": "utf-8", "NO_PROXY": "127.0.0.1,localhost,::1",
           "REDIS__DB": str(settings.redis.db), "LANGFUSE__ENABLED": "0",
           "PYTHONDONTWRITEBYTECODE": "1",
           "B6_LIE_REMOVE": "1" if control else "0"}
    ms = int(os.environ.get("B6_RUN_MS", "6000"))
    procs = [subprocess.Popen([sys.executable, "-B", "-c", WORKER, role, sid, str(ms)],
                              cwd="E:/Codeharness", env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, encoding="utf-8")
             for role in ("drain", "drain", "mutate")]
    try:
        outs = []
        for p in procs:
            so, se = p.communicate(timeout=120)
            if p.returncode != 0:
                sys.exit(f"exit 1：子进程退出码 {p.returncode}：{se[-400:]}")
            outs.append(json.loads(so.strip().splitlines()[-1]))
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
        import redis
        redis.Redis.from_url(settings.redis.to_url(), decode_responses=True).delete(f"ch:chat:{sid}")

    drains = [o for o in outs if o["role"] == "drain"]
    mut = next(o for o in outs if o["role"] == "mutate")
    got = [i for o in drains for i in o["got"]]
    set_got, set_added = set(got), set(mut["added"])
    ok_rm = {i for i, r in mut["removed"] if r}
    refused = {i for i, r in mut["removed"] if not r}
    bad = []
    # ②不重复投递：两个 worker 的收获必须互不相交（Redis 那台是逐条 LPOP，交得上就说明哪天
    #    退回 LRANGE+DEL 的写法了——那正是文件头写明的「会喂两遍」的形状）
    inter = set(drains[0]["got"]) & set(drains[1]["got"])
    if inter:
        bad.append(f"②重复投递：两个 worker 各收到同一件 {sorted(inter)[:5]}（共 {len(inter)} 件）")
    if len(got) != len(set_got):
        bad.append("②同一方内部也收到重复 id")
    # ①同一件不许两种结局：被取走的又「撤回成功」＝两个进程各自记账、谁都不知道对方拿走了
    ghost = ok_rm & set_got
    if ghost:
        bad.append(f"①已投递又被报「撤回成功」：{sorted(ghost)[:5]}（共 {len(ghost)} 件）"
                   " ⇒ 这条通道会把同一句插话用两次，或反过来以为自己撤掉了其实没撤")
    if sum(1 for _, r in mut["removed"] if r) != len(ok_rm):
        bad.append("①同一件被撤回成功两次")
    # ③不丢件：每一条投出去的，最终必须在「被某方取走」或「被撤回成功」里有一席
    lost = {i for i in set_added if i not in set_got | ok_rm}
    if lost:
        bad.append(f"③丢件：{len(lost)} 条既没被取走也没被撤掉，例如 {sorted(lost)[:5]}"
                   "（C7 那件整件就是「这条通道丢过一次消息」的代价）")
    # 「撤回被拒」本身是**正确结局**（那条已被某个 worker 取走，或被我们先撤过一次），所以它不是红、
    # 而是要能归因：拒掉的每一条必须能在「别人的收获」或「我们之前的成功撤回」里找到去向。
    orphan = {i for i in refused if i not in set_got | ok_rm}
    if orphan:
        bad.append(f"①撤回被拒却查无去向（既没被取走、也没被我们先撤过）：{sorted(orphan)[:5]}"
                   f"（共 {len(orphan)} 条）")
    print(f"  读数：投出 {len(set_added)} 条 ｜ drain 收到 {len(got)} 条（A {len(drains[0]['got'])} / "
          f"B {len(drains[1]['got'])}）｜ 撤回成功 {len(ok_rm)} 条 ｜ 撤回被拒 {len(refused)} 条"
          f"（全部归因到「已被取走」）｜ 真子进程 {len(procs)} 个（2 个 drain + 1 个投撤）")
    if control:
        fired = [b for b in bad if b.startswith("①")]
        print("阳性对照：" + (f"撒谎的 remove 一上场，① 当场红 {len(fired)} 格 ⇒ 这条判据有牙："
                              f"{fired[0][:80]}" if fired else
                             "对照没红 ⇒ 「①」是恒真判据，本件的结论一律作废（先修判据再谈读数）："
                             + ("；".join(bad) or "全绿")))
        return 0 if fired else 2
    if bad:
        print("判据未绿：" + "；".join(bad))
        return 2
    print(f"判据：真两进程（跨进程各自建队）下 {len(set_added)} 条各有归属——无重复投递、无丢件、"
          "无「同一件两种结局」；撤回被拒的 " + str(len(refused)) + " 条都能归因到已被某个 worker 取走")
    return 0


if __name__ == "__main__":
    sys.exit(main())
