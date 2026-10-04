"""翻案激活自检（C158/C159 的收尾工具）：`.env` 三行加完后跑这一条，看四件事。

  ① 配置生效：MEMORY_MODE=rerank / MEMORY_MIN_SCORE=0.0 读到了；
  ② 端点可达：base_url 非空且真 HTTP 往返一发达出去；
  ③ 真精排出分：relevance_score 拿得到（None = 端点失败被降级——额度尽/服务离线都长这样）；
  ④ 排序正确：相关的排前面（样例对：问红烧肉，做法那条要赢过天气）。

跑法（无需重启后端；本脚本现读 .env）：
  PYTHONIOENCODING=utf-8 F:/anaconda/python.exe -B scripts/verify_memory_flip.py

全部 ✅ ⇒ 记忆重排在本部署已生效。❌ 的每一行都带该修哪里的指向。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codeharness.configs.settings import settings          # noqa: E402
from codeharness.memory.longterm import LongTermMemory     # noqa: E402

ok = True

rf = settings.recall_floor
if rf.memory_mode != "rerank":
    print(f"❌ RECALL_FLOOR__MEMORY_MODE={rf.memory_mode!r}（要 'rerank'）——.env 那行没加或没保存")
    ok = False
else:
    print("✅ MEMORY_MODE=rerank 已生效")
if rf.memory_min_score not in (0, 0.0):
    print(f"❌ RECALL_FLOOR__MEMORY_MIN_SCORE={rf.memory_min_score!r}（要 0.0——T=0 纯重排免标定，"
          "C158：T≥0.2 全线灾难）")
    ok = False
else:
    print("✅ MEMORY_MIN_SCORE=0.0 已生效（纯重排不设线）")

url = settings.reranker.base_url
if not url:
    print("❌ RERANKER__BASE_URL 为空——validator 会直接拒绝启动，先补这行")
    sys.exit(1)
print(f"✅ RERANKER__BASE_URL={url[:60]}")

hits = [SimpleNamespace(payload={"text": t}) for t in
        ("红烧肉要焯水后小火炖", "今天天气不错")]
import asyncio
scored = asyncio.run(LongTermMemory.rerank_scored(None, "怎么做红烧肉", hits, 2))
if any(s is None for _, s in scored):
    print("❌ 精排端点没给出分（None = 调用失败被降级）——额度尽（403）/服务离线/URL 形状不对，"
          "看上面那声 warning 的真实异常")
    ok = False
else:
    best = scored[0]
    if best[0].payload["text"] == "红烧肉要焯水后小火炖" and best[1] > 0.5:
        print(f"✅ 真精排出分且排序正确：top score={best[1]:.4f}")
    else:
        print(f"⚠ 出分了但形状意外：{[(h.payload['text'][:12], round(s or -1, 4)) for h, s in scored]}"
              "——CE 分数极化是已知形状（C158），只要 top 是相关那条就行")
        ok = ok and best[0].payload["text"] == "红烧肉要焯水后小火炖"

print("\n翻案激活：" + ("全部就绪 ✅" if ok else "未就绪 ❌（按上面逐行修）"))
sys.exit(0 if ok else 1)
