"""门禁覆盖率的**观察式**统计：每格到底判了没判，从它自己印出来的东西算，不靠「记得印一行」。

为什么不做成「各格自己登记」：`s4 t44` 那条死门就是这套机制坏掉的形状——守卫恒假时它**连那行都不印**
（09-29 现证，账在 `plan/platform-infra.md` §1.3）；比恒 skip 更糟的是**静默早退**（一个字不印），
那种格子连"我跳过了"都没留下。所以由 runner 捕获每格的 stdout 分三类：判了 / 跳了（印了「跳过/skip」）/
**空转（什么都没印）**，并把清单拼进收口行——**绿不绿与判没判从此是两栏**。
"""
import contextlib
import io
import re
import sys

SKIP_LINE = re.compile(r"^\s*(?:⚠+\s*)?(?:skip\s+\w+|\w+\s+跳过[（:])")
"""**按行首形状**认「这格跳过了」，不认散文里出现的「跳过」二字。

09-29 现证的误报：`t32` 的正文是「精排未配置：默认值即跳过——零 HTTP…」（那是它的**结论**）、
`t43` 的 ok 行里有「失败后跳过不重复计数」——用子串匹配会把这两格报成没判，
而误报的覆盖率清单和漏报一样有害（下一个人就不再信这行）。所以只认 `skip tNN…` / `tNN 跳过（…）` 这两种形状。
"""


def run_all(checks, ok_line=False):
    """按序跑每格，返回 `(跳过的格名, 空转的格名)`。

    异常**照常外抛**（吞掉异常会把红变成另一种绿）；抛之前先把该格已印的输出交回屏幕，否则现场没了。
    `ok_line=True` 给「格子自己不印、由循环统一印 `ok 格名`」的门禁（s4 就是这惯例）：那种门里
    「一个字都没印」是**常态**，拿它判空转会把 49 格全冤枉成没跑（09-29 现证过这个误判）。
    所以那种门只有「跳过」这一类是有意义的信号，空转那栏恒空。
    """
    skipped, silent = [], []
    for fn in checks:
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                fn()
        finally:
            out = buf.getvalue()
            if out:
                sys.stdout.write(out)
        if any(SKIP_LINE.match(l) for l in out.splitlines()):
            skipped.append(fn.__name__)
        elif not out.strip() and not ok_line:
            silent.append(fn.__name__)
        if ok_line:
            print(f"  ok  {fn.__name__}")
    return skipped, silent



def verdict(skipped, silent):
    """收口行后缀：没有缺口时是「0 跳过、0 空转」，有缺口就把格子名列全。"""
    if not skipped and not silent:
        return "，0 跳过、0 空转"
    parts = []
    if skipped:
        parts.append(f"跳过 {len(skipped)} 格 {skipped}")
    if silent:
        parts.append(f"**空转 {len(silent)} 格 {silent}**")
    return "，⚠ " + "；".join(parts)
