"""令牌层对比度体检（WCAG 相对亮度），值直接从 tokens.css 读，避免和源漂移。
用法：python frontend/scripts/contrast_report.py
正文/次级文字要求 ≥ 4.5:1；12px 装饰性 caption 与参考项目一样只要求 ≥ 3:1。"""
import re
import sys
from pathlib import Path

TOKENS = Path(__file__).resolve().parent.parent / "src" / "styles" / "tokens.css"

# (前景令牌, 背景令牌, 要求, 说明)
PAIRS = [
    ("--dsw-alias-label-primary", "--dsw-alias-bg-base", 4.5, "正文 / 底"),
    ("--dsw-alias-label-primary", "--dsw-specific-sidebar-fill", 4.5, "正文 / 侧栏"),
    ("--dsw-alias-label-secondary", "--dsw-alias-bg-base", 4.5, "次级文字 / 底"),
    ("--dsw-alias-label-tertiary", "--dsw-alias-bg-base", 4.5, "三级文字 / 底"),
    ("--dsw-alias-label-caption", "--dsw-alias-bg-base", 3.0, "12px 装饰 caption / 底"),
    ("--dsw-alias-state-business-primary", "--dsw-alias-bg-base", 3.0, "业务蓝（链接/强调）/ 底"),
    ("--dsw-alias-state-error-primary", "--dsw-alias-bg-base", 4.5, "错误红 / 底"),
    ("--dsw-alias-state-success-primary", "--dsw-alias-bg-base", 3.0, "成功绿（状态点）/ 底"),
    ("--dsw-alias-state-warn-label", "--dsw-alias-state-warn-tertiary", 4.5, "警示文字 / 警示底"),
    ("--dsw-static-neutral-bluish-00", "--dsw-alias-tooltip-bg", 4.5, "tooltip 白字 / 深底"),
    ("--dsw-static-neutral-bluish-00", "--dsw-alias-toast-bg", 4.5, "toast 白字 / 深底"),
    ("--dsw-alias-label-primary", "--dsw-specific-bubble", 4.5, "用户气泡文字 / 气泡底"),
    ("--dsw-alias-label-primary", "--dsw-alias-markdown-inline-code", 4.5, "行内代码 / 底"),
]


BLOCK = re.compile(r"(^|\})\s*(body(?:\[data-ds-dark-theme\])?)\s*\{([^}]*)\}", re.M)
DECL = re.compile(r"(--[\w-]+):\s*([^;]+);")


def blocks(text: str):
    """按选择器切块：只有 body{…} 进浅色表，body[data-ds-dark-theme]{…} 进深色覆盖表。
    不能对整份文件跑一条全局 --* 正则——那样同名令牌会被后面的深色块覆写，
    深浅两遍算出同一个数（那就等于没测）。"""
    light, dark = {}, {}
    for m in BLOCK.finditer(text):
        sel, body = m.group(2), m.group(3)
        table = dark if "dark-theme" in sel else light
        table.update(dict(DECL.findall(body)))
    return light, dark


def resolve(name: str, table: dict, seen=()):
    raw = table.get(name)
    if raw is None:
        return None
    ref = re.fullmatch(r"var\((--[\w-]+)\)", raw.strip())
    if ref:
        key = ref.group(1)
        if key in seen:
            return None
        return resolve(key, {**table}, seen + (name,))
    return raw.strip()


def rgb_of(value: str, table: dict):
    if value is None:
        return None
    m = re.fullmatch(r"rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)(?:[,\s]+([\d.]+))?\s*\)", value)
    if not m:
        return None
    r, g, b = (int(m.group(i)) for i in (1, 2, 3))
    a = float(m.group(4)) if m.group(4) else 1.0
    return (r, g, b, a)


def over(fg, bg_rgb):
    """把带 alpha 的前景叠到不透明背景上。"""
    r, g, b, a = fg
    return tuple(round(f * a + bv * (1 - a)) for f, bv in zip((r, g, b), bg_rgb))


def lum(c):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = c[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def ratio(fg, bg):
    a, b = lum(fg), lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def main():
    if not TOKENS.exists():
        sys.exit(f"找不到 {TOKENS}")
    text = TOKENS.read_text(encoding="utf-8")
    light, dark_over = blocks(text)
    tables = {"浅色": light, "深色": {**light, **dark_over}}
    bad = 0
    for theme, table in tables.items():
        print(f"\n[{theme}]")
        for fg_name, bg_name, need, label in PAIRS:
            bg = rgb_of(resolve(bg_name, table), table)
            fg_raw = rgb_of(resolve(fg_name, table), table)
            if not bg or not fg_raw:
                print(f"  ?? {label}: 取值失败 {fg_name} on {bg_name}")
                bad += 1
                continue
            r = ratio(over(fg_raw, bg), bg)
            flag = "OK " if r >= need else "LOW"
            if r < need:
                bad += 1
            print(f"  {flag} {r:5.2f}:1 (要求 {need})  {label}")
    print(f"\n{'全部达标' if not bad else str(bad) + ' 项未达标'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
