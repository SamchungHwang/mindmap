"""markmap 렌더러 — 노드 트리 → Markdown(markmap 문법) → 단일 HTML (CDN autoloader).

- 1단계 가지는 `##`, 그 아래는 들여쓴 `-` 목록.
- 기본 접힘: A9 근거 자료, B2.4 사실 로그, C1.1 holdings, C6.1 로그 (`<!-- markmap: fold -->`).
- 요약 뷰(summary=True, 보유 맵): B0 · B2.3 · B6 · 다음 확인일만.
"""

from __future__ import annotations

import html
from pathlib import Path

from ..models import HoldingMap, MindMap, Node
from .format import node_line, root_title

FOLD_BY_DEFAULT = {"A9", "B2.4", "C1.1", "C6.1", "A8.2", "B8.4"}
FOLD_MARK = " <!-- markmap: fold -->"

MARKMAP_AUTOLOADER = "https://cdn.jsdelivr.net/npm/markmap-autoloader@0.18"


def _frontmatter(title: str, expand_level: int, max_width: int = 360) -> str:
    return (
        "---\n"
        f"title: {title}\n"
        "markmap:\n"
        f"  colorFreezeLevel: 2\n"
        f"  initialExpandLevel: {expand_level}\n"
        f"  maxWidth: {max_width}\n"
        "  duration: 300\n"
        "---\n\n"
    )


def _render_children(m: MindMap, parent: Node, depth: int, lines: list[str], fold: set[str],
                     max_depth: int | None) -> None:
    if max_depth is not None and depth > max_depth:
        return
    for c in m.children(parent.id):
        indent = "  " * (depth - 1)
        txt = node_line(c)
        if c.id in fold:
            txt += FOLD_MARK
        lines.append(f"{indent}- {txt}")
        _render_children(m, c, depth + 1, lines, fold, max_depth)


def to_markdown(m: MindMap, *, summary: bool = False, fold: set[str] | None = None,
                max_depth: int | None = None, expand_level: int = 2) -> str:
    """맵 → markmap 마크다운."""
    fold = FOLD_BY_DEFAULT if fold is None else fold
    root = m.root()
    title = root_title(m)
    out: list[str] = [_frontmatter(title.replace("*", ""), expand_level), f"# {title}"]

    if summary and isinstance(m, HoldingMap):
        for nid in ("B0", "B0.1", "B2.3", "B6", "B3.4", "B4.1"):
            if nid in m:
                out.append(f"## {node_line(m[nid])}")
        nxt = sorted({d.driver.next_check for d in m.drivers() if d.driver and d.driver.next_check})
        if nxt:
            out.append(f"## 다음 확인일: {nxt[0].isoformat()}")
        fired = [n for n in m.rules() if n.rule and n.rule.status == "fired"]
        if fired:
            out.append("## 발동 규칙")
            for n in fired:
                out.append(f"- {node_line(n)}")
        return "\n".join(out) + "\n"

    # 중심 노드의 보조 필드(A0.x / B0.x)는 루트 바로 아래 줄로
    first_level = m.children(root.id)
    aux = [c for c in first_level if "." in c.id]
    branches = [c for c in first_level if "." not in c.id]
    for c in aux:
        out.append(f"- {node_line(c)}")
    for br in branches:
        txt = node_line(br)
        if br.id in fold:
            txt += FOLD_MARK
        out.append(f"\n## {txt}")
        _render_children(m, br, 1, out, fold, max_depth)
    return "\n".join(out) + "\n"


def to_html(m: MindMap, *, summary: bool = False, fold: set[str] | None = None,
            max_depth: int | None = None, expand_level: int = 2, autoloader_url: str = MARKMAP_AUTOLOADER) -> str:
    """단일 HTML. markmap-autoloader 가 `.markmap` 블록 안의 마크다운을 렌더링한다 (인터넷 필요)."""
    md = to_markdown(m, summary=summary, fold=fold, max_depth=max_depth, expand_level=expand_level)
    safe_md = md.replace("</script", "<\\/script")
    title = html.escape(root_title(m).replace("*", ""))
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  /* 테마 토큰. markmap 은 자체 변수(--markmap-text-color 등)로 글자색을 정하므로
     배경만 바꾸면 어두운 글자가 남는다 → 아래에서 markmap 변수까지 함께 덮어쓴다. */
  :root {{
    --bg: #ffffff; --fg: #1f2328; --muted: #6b7280;
    --code-bg: #f3f4f6; --code-fg: #374151; --hl: #fff3b0; --btn: #e5e7eb;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg: #0f1115; --fg: #e8eaed; --muted: #a3a9b1;
      --code-bg: #262a31; --code-fg: #e3e6ea; --hl: #5c4d00; --btn: #2a2f37;
    }}
  }}
  :root[data-theme="dark"] {{
    --bg: #0f1115; --fg: #e8eaed; --muted: #a3a9b1;
    --code-bg: #262a31; --code-fg: #e3e6ea; --hl: #5c4d00; --btn: #2a2f37;
  }}
  html, body {{ margin: 0; height: 100%; background: var(--bg); color: var(--fg); }}
  /* markmap 이 런타임에 주입하는 스타일(.markmap {{ --markmap-text-color:#333 }})보다 우선하도록 선택자 특이도를 높인다 */
  body .markmap {{
    position: absolute; inset: 0;
    color: var(--fg);
    --markmap-text-color: var(--fg);
    --markmap-code-bg: var(--code-bg);
    --markmap-code-color: var(--code-fg);
    --markmap-circle-open-bg: var(--bg);
    --markmap-highlight-bg: var(--hl);
    --markmap-font: 400 15px/1.5 "Pretendard", "Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans KR", system-ui, sans-serif;
    font: var(--markmap-font);
  }}
  .markmap > svg {{ width: 100%; height: 100%; }}
  body .markmap .markmap-foreign {{ color: var(--fg); }}
  .markmap strong {{ font-weight: 700; }}
  .markmap sub {{ font-size: .78em; color: var(--muted); vertical-align: baseline; margin-left: .25em; }}
  .markmap em {{ color: var(--muted); }}
  .markmap code {{ font-size: .9em; padding: 0 .3em; border-radius: 4px; }}
  #theme-toggle {{
    position: fixed; top: 10px; right: 12px; z-index: 10;
    border: 0; border-radius: 999px; padding: 6px 12px; cursor: pointer;
    background: var(--btn); color: var(--fg); font: 500 13px/1 system-ui, sans-serif; opacity: .85;
  }}
  #theme-toggle:hover {{ opacity: 1; }}
</style>
<script src="{autoloader_url}"></script>
</head>
<body>
<button id="theme-toggle" type="button" title="밝게/어둡게 전환">☀ / ☾</button>
<div class="markmap">
<script type="text/template">
{safe_md}
</script>
</div>
<script>
(function () {{
  var root = document.documentElement, key = "kstock-mindmap-theme";
  try {{ var saved = localStorage.getItem(key); if (saved) root.setAttribute("data-theme", saved); }} catch (e) {{}}
  document.getElementById("theme-toggle").addEventListener("click", function () {{
    var dark = root.getAttribute("data-theme") === "dark" ||
      (!root.getAttribute("data-theme") && window.matchMedia("(prefers-color-scheme: dark)").matches);
    var next = dark ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try {{ localStorage.setItem(key, next); }} catch (e) {{}}
  }});
}})();
</script>
</body>
</html>
"""


def write_html(m: MindMap, path: str | Path, **kw) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(to_html(m, **kw), encoding="utf-8")
    return p


def write_markdown(m: MindMap, path: str | Path, **kw) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(to_markdown(m, **kw), encoding="utf-8")
    return p


__all__ = ["to_markdown", "to_html", "write_html", "write_markdown", "FOLD_BY_DEFAULT", "MARKMAP_AUTOLOADER"]
