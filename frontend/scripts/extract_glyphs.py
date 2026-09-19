"""把参考项目 ui-primitives 的 React 图标组件抽成 Vue 可用的字形表。

一次性 vendoring 工具：源仓库路径变了就改 SRC 重跑，产物 frontend/src/components/ui/glyphs.ts
是纯数据，可以直接进版本库。
"""

import json
import pathlib
import re
import sys

SRC = pathlib.Path(
    r"E:\deepseek-harness\packages\client\ui-primitives\src\icons\index.tsx"
)
OUT = pathlib.Path(__file__).resolve().parent.parent / "src" / "components" / "ui" / "glyphs.ts"

# JSX 属性名 → SVG 属性名
ATTR_FIX = {
    "fillRule": "fill-rule",
    "clipRule": "clip-rule",
    "clipPath": "clip-path",
    "strokeWidth": "stroke-width",
    "strokeLinecap": "stroke-linecap",
    "strokeLinejoin": "stroke-linejoin",
    "stopColor": "stop-color",
    "stopOpacity": "stop-opacity",
}

BLOCK_HEAD = re.compile(r"export const (Icon[A-Za-z0-9]+)")
SVG = re.compile(r"<svg\b.*</svg>", re.S)


def extract(svg: str):
    open_tag = svg[: svg.index(">") + 1]
    vb = re.search(r'viewBox="([^"]+)"', open_tag)
    body = svg[svg.index(">") + 1 : svg.rindex("</svg>")]
    # 去掉 React 专用属性与模板插值
    body = re.sub(r"\s*className=\{[^}]*\}", "", body)
    body = re.sub(r"\s*width=\{[^}]*\}\s*height=\{[^}]*\}", "", body)
    for k, v in ATTR_FIX.items():
        body = re.sub(rf"\b{k}=", f"{v}=", body)
    body = re.sub(r"\s+", " ", body).strip()
    return (vb.group(1) if vb else "0 0 16 16"), body


def main():
    if not SRC.exists():
        sys.exit(f"源图标文件不存在：{SRC}")
    text = SRC.read_text(encoding="utf-8")
    heads = list(BLOCK_HEAD.finditer(text))
    out, skipped, failed = {}, [], []
    for i, m in enumerate(heads):
        chunk = text[m.end() : heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        svg_m = SVG.search(chunk)
        if not svg_m:
            failed.append(m.group(1))
            continue
        vb, body = extract(svg_m.group(0))
        if "{" in body or "}" in body:  # 含插值的动态 glyph 不自动搬
            skipped.append(m.group(1))
            continue
        out[m.group(1)] = {"vb": vb, "html": body}
    decl = "/* 自动生成，勿手改：python frontend/scripts/extract_glyphs.py\n"
    decl += f"   来源 {SRC} —— {len(out)} 个 glyph */\n\n"
    decl += "export interface Glyph {\n  vb: string\n  html: string\n}\n\n"
    decl += "export const GLYPHS: Record<string, Glyph> = " + json.dumps(
        out, ensure_ascii=False, indent=1
    ) + "\n"
    OUT.write_text(decl, encoding="utf-8")
    print(f"写入 {OUT} —— {len(out)} 个 glyph")
    if failed:
        print(f"未匹配到 svg 的 {len(failed)} 个：{', '.join(failed)}")
    if skipped:
        print(f"跳过含插值的 {len(skipped)} 个：{', '.join(skipped)}")


if __name__ == "__main__":
    main()
