"""자체 마인드맵 렌더러 — 중심 노드에서 **양쪽(좌우)으로 균형 있게**, 또는 **방사형**으로 펼친다.

markmap 은 왼쪽→오른쪽 한 방향 트리라 중심이 왼쪽 끝에 붙는다. 여기서는 외부 라이브러리 없이
(CDN·인터넷 불필요) 바닐라 JS 로 레이아웃을 직접 계산한다.

레이아웃 (HTML 안에서 바꿀 수 있다)
  balanced  1단계 가지를 좌/우로 나눈다 — 각 쪽의 세로 길이가 비슷해지도록 큰 가지부터 번갈아 배정.
            자식은 부모 바깥쪽으로 세로로 쌓이므로 상·하·좌·우로 고르게 퍼진다 (XMind 의 '마인드맵' 형).
  radial    잎 수에 비례해 각도를 나눠 동심원에 놓는다. 라벨이 길면 겹칠 수 있어 보조용.
  right     markmap 과 같은 오른쪽 한 방향.

노드 글자는 render.format.node_line 을 그대로 쓰되 마크다운 조각(**, `, _)만 HTML 로 바꾼다.
접기(기본 접힘: A8 근거 자료, B2.4 사실 로그 등)·펼치기·팬·줌·맞춤·☀/☾ 테마를 지원한다.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from ..models import HoldingMap, MindMap, NewMap, Node
from .format import node_line, root_title
from .markmap import FOLD_BY_DEFAULT

_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_CODE = re.compile(r"`([^`]+)`")
_MD_ITAL = re.compile(r"(?<![\w가-힣])_([^_]+)_(?![\w가-힣])")


def _md_inline(text: str) -> str:
    """node_line 이 내는 마크다운 조각 → HTML. <sub> 는 그대로 통과."""
    parts = re.split(r"(<sub>.*?</sub>)", text)
    out = []
    for p in parts:
        if p.startswith("<sub>"):
            out.append(p)
            continue
        esc = html.escape(p, quote=False)
        esc = _MD_CODE.sub(r"<code>\1</code>", esc)
        esc = _MD_BOLD.sub(r"<b>\1</b>", esc)
        esc = _MD_ITAL.sub(r"<i>\1</i>", esc)
        out.append(esc)
    return "".join(out)


_SUB = re.compile(r"<sub>(.*?)</sub>")


def _plain(text: str) -> str:
    """node_line 의 마크다운 조각(**, `, _) 과 <sub> 를 걷어낸 평문 — 복사용."""
    t = _SUB.sub(r" (\1)", text)
    t = _MD_CODE.sub(r"\1", t)
    t = _MD_BOLD.sub(r"\1", t)
    t = _MD_ITAL.sub(r"\1", t)
    return html.unescape(" ".join(t.split()))


def _fmt_value(v: Any, indent: str = "  ") -> list[str]:
    if v is None:
        return []
    if isinstance(v, dict):
        return [f"{indent}{k}: {x}" for k, x in v.items() if x not in (None, "", [], {})]
    if isinstance(v, list):
        out: list[str] = []
        for x in v:
            out += _fmt_value(x, indent) if isinstance(x, (dict, list)) else [f"{indent}- {x}"]
        return out
    return [f"{indent}{v}"]


def _detail(n: Node) -> str:
    """노드 1개의 전체 정보 — 상세 패널·'상세 복사' 용 여러 줄 평문. 값·질문·출처·날짜·태그와 하위 스키마(드라이버·시나리오·규칙·이벤트) 를 다 적는다."""
    lines = [f"[{n.id}] {n.label}"]
    sub = None
    for name, obj in (("드라이버", n.driver), ("시나리오", n.scenario), ("규칙", n.rule), ("이벤트", n.event)):
        if obj is not None:
            sub = (name, obj)
            break
    if sub is not None:
        name, obj = sub
        lines.append(f"{name}:")
        lines += _fmt_value(obj.model_dump(mode="json", exclude_none=True))
    elif n.value is not None:
        lines.append("값:")
        lines += _fmt_value(n.value)
    if n.question:
        lines.append(f"질문: {n.question}")
    if n.source is not None and (n.source.name or n.source.url):
        lines.append("출처: " + " ".join(x for x in (n.source.name, n.source.url) if x))
    if n.as_of:
        lines.append(f"기준일: {n.as_of.isoformat()}")
    if n.status:
        lines.append(f"상태: {n.status.value}")
    if n.tags:
        lines.append("태그: " + ", ".join(n.tags))
    if n.links:
        lines.append("연결: " + ", ".join(n.links))
    return "\n".join(lines)


def _kind(n: Node) -> str:
    if n.driver is not None:
        return "driver"
    if n.scenario is not None:
        return "scenario"
    if n.rule is not None:
        return "rule"
    if n.event is not None:
        return "event"
    return n.node_type.value


_EDIT_KEYS = ("label", "question", "value", "value_type", "unit", "node_type", "enum_options", "source", "as_of", "confidence", "status",
              "next_check", "weight", "links", "tags", "formula")


def _edit_fields(n: Node, m: MindMap | None = None) -> dict[str, Any]:
    """편집 폼이 채우는 노드 스냅숏 — generate.edits.ALLOWED_FIELDS 와 같은 이름. 하위 스키마는 sub={kind,data}.
    `suggest` = 리포트 기반 제안값 (meta.draft.suggestions, 설계 v4 §6) — 폼이 빈 칸 옆에 「채우기」 로 보인다."""
    d = n.model_dump(mode="json")
    out: dict[str, Any] = {k: d.get(k) for k in _EDIT_KEYS}
    kind = _kind(n)
    out["sub"] = {"kind": kind, "data": d[kind]} if kind in ("driver", "scenario", "rule", "event") else None
    if m is not None:
        rows = ((m.meta.get("draft") or {}).get("suggestions") or {}).get(n.id) or []
        out["suggest"] = [{"field": s.get("field"), "value": s.get("value"), "label": s.get("label"), "kind": s.get("kind"),
                           "report_ids": s.get("report_ids") or [], "accepted": bool(s.get("accepted_at")), "prefilled": bool(s.get("prefilled"))}
                          for s in rows]
    return out


def _tree(m: MindMap, parent: Node, fold: set[str], max_depth: int | None, depth: int) -> dict[str, Any]:
    line = node_line(parent)
    node: dict[str, Any] = {"id": parent.id, "html": _md_inline(line), "kind": _kind(parent), "text": _plain(line),
                            "detail": _detail(parent), "fold": parent.id in fold, "children": [], "edit": _edit_fields(parent, m)}
    if max_depth is None or depth < max_depth:
        node["children"] = [_tree(m, c, fold, max_depth, depth + 1) for c in m.children(parent.id)]
    return node


def build_tree(m: MindMap, *, summary: bool = False, fold: set[str] | None = None,
               max_depth: int | None = None) -> dict[str, Any]:
    """맵 → 중첩 dict. 중심 노드의 보조 필드(A0.x / B0.x)는 중심의 첫 자식 묶음으로 둔다.

    노드마다 `edit`(편집 폼 스냅숏) 가 붙는다. 묶음·요약 같은 가짜 노드는 `locked` (편집·삭제 불가; `A0.*` 묶음엔 자식 추가 가능)."""
    fold = FOLD_BY_DEFAULT if fold is None else fold
    root = m.root()
    title = root_title(m).replace("*", "")
    tree: dict[str, Any] = {"id": root.id, "html": f"<b>{html.escape(title)}</b>", "kind": "root", "fold": False, "children": [],
                            "text": title, "detail": _detail(root), "edit": _edit_fields(root, m)}
    if summary and isinstance(m, HoldingMap):
        for nid in ("B0", "B0.1", "B2.3", "B6", "B3.4", "B4.1"):
            if nid in m:
                tree["children"].append({"id": f"s:{nid}", "html": _md_inline(node_line(m[nid])), "kind": _kind(m[nid]), "locked": True,
                                         "text": _plain(node_line(m[nid])), "detail": _detail(m[nid]), "fold": False, "children": []})
        nxt = sorted({d.driver.next_check for d in m.drivers() if d.driver and d.driver.next_check})
        if nxt:
            tree["children"].append({"id": "next", "html": f"다음 확인일: {nxt[0].isoformat()}", "kind": "computed", "fold": False, "children": [],
                                     "text": f"다음 확인일: {nxt[0].isoformat()}", "detail": f"다음 확인일: {nxt[0].isoformat()}", "locked": True})
        return tree
    first = m.children(root.id)
    aux = [c for c in first if "." in c.id]
    branches = [c for c in first if "." not in c.id]
    if aux:
        tree["children"].append({"id": f"{root.id}.*", "html": "기본 정보", "kind": "question", "fold": False, "text": "기본 정보", "detail": "기본 정보",
                                 "locked": True, "children": [_tree(m, c, fold, max_depth, 2) for c in aux]})
    for b in branches:
        tree["children"].append(_tree(m, b, fold, max_depth, 1))
    return tree


# ============================================================================ HTML
_CSS = """
:root { --bg:#ffffff; --fg:#1f2328; --muted:#6b7280; --line:#c9ced6; --box:#ffffff; --box-border:#d9dee5;
        --code-bg:#f3f4f6; --root-bg:#1f4e79; --root-fg:#ffffff; --btn:#e5e7eb; --shadow:0 1px 2px rgba(0,0,0,.08); }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg:#0f1115; --fg:#e8eaed; --muted:#a3a9b1; --line:#3a414b; --box:#171a20; --box-border:#2b313a;
  --code-bg:#262a31; --root-bg:#2d6cb0; --root-fg:#ffffff; --btn:#2a2f37; --shadow:none; } }
:root[data-theme="dark"] { --bg:#0f1115; --fg:#e8eaed; --muted:#a3a9b1; --line:#3a414b; --box:#171a20; --box-border:#2b313a;
  --code-bg:#262a31; --root-bg:#2d6cb0; --root-fg:#ffffff; --btn:#2a2f37; --shadow:none; }
html, body { margin:0; height:100%; background:var(--bg); color:var(--fg);
  font: 400 14px/1.45 "Pretendard", "Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans KR", system-ui, sans-serif; }
#stage { position:absolute; inset:0; overflow:hidden; cursor:grab; }
#stage.dragging { cursor:grabbing; }
#viewport { position:absolute; left:0; top:0; transform-origin:0 0; }
#edges { position:absolute; left:0; top:0; overflow:visible; pointer-events:none; }
#edges path { fill:none; stroke-width:2; opacity:.9; }
.node { position:absolute; box-sizing:border-box; width:max-content; max-width:380px; padding:5px 10px; border-radius:8px;
  background:var(--box); border:1px solid var(--box-border); border-left-width:4px; box-shadow:var(--shadow);
  white-space:normal; word-break:keep-all; overflow-wrap:anywhere; cursor:default; user-select:text; }
.node.has-kids { cursor:pointer; }
.node.selected { outline:2px solid var(--root-bg); outline-offset:1px; }
.node .copy, .node .pen { position:absolute; top:-9px; display:none; width:20px; height:20px; line-height:18px; text-align:center;
  border-radius:50%; background:var(--btn); color:var(--fg); border:1px solid var(--box-border); font-size:12px; cursor:pointer; user-select:none; }
.node .copy { right:-9px; }
.node .pen { right:14px; }
.node:hover .copy, .node:hover .pen { display:block; }
.node .copy:hover, .node .pen:hover { background:var(--root-bg); color:#fff; }
.node.edited { border-style:dashed; }
.node.added { border-style:dashed; border-left-style:solid; }
.node .mark { font-size:.75em; color:#d9822b; margin-right:.3em; }
#editor { position:fixed; top:0; right:0; bottom:0; width:min(520px, 94vw); z-index:25; display:none; flex-direction:column; gap:8px;
  background:var(--box); color:var(--fg); border-left:1px solid var(--box-border); box-shadow:-6px 0 24px rgba(0,0,0,.2); padding:14px; }
#editor.show { display:flex; }
#editor h3 { margin:0; font-size:14px; padding-right:60px; }
#editor .close { position:absolute; top:10px; right:12px; }
#editor form { flex:1; overflow:auto; display:flex; flex-direction:column; gap:8px; padding-right:4px; }
#editor label { display:flex; flex-direction:column; gap:3px; font-size:12px; color:var(--muted); }
#editor label.inline { flex-direction:row; align-items:center; gap:8px; }
#editor input, #editor select, #editor textarea { font:13px/1.4 inherit; color:var(--fg); background:var(--bg); border:1px solid var(--box-border);
  border-radius:6px; padding:5px 8px; width:100%; box-sizing:border-box; }
#editor textarea { min-height:56px; resize:vertical; font-family: inherit; }
#editor textarea.json { font-family: ui-monospace, "Cascadia Mono", Consolas, "D2Coding", monospace; }
#editor fieldset { border:1px solid var(--box-border); border-radius:8px; padding:8px 10px; display:flex; flex-direction:column; gap:8px; margin:0; }
#editor legend { font-size:12px; color:var(--muted); padding:0 4px; }
#editor .adv { display:none; flex-direction:column; gap:8px; }
#editor fieldset.showadv .adv { display:flex; }
#editor .row { display:flex; gap:6px; flex-wrap:wrap; }
#editor .row.two > label { flex:1 1 45%; }
#editor button { border:0; border-radius:999px; padding:6px 12px; cursor:pointer; background:var(--btn); color:var(--fg); font:500 13px/1 system-ui, sans-serif; }
#editor button.primary { background:var(--root-bg); color:#fff; }
#editor button.danger { background:#c23b3b; color:#fff; }
#editor .err { color:#c23b3b; font-size:12px; white-space:pre-wrap; }
#editor .sugg { display:flex; gap:6px; align-items:center; flex-wrap:wrap; font-size:12px; color:var(--muted); margin-top:2px; }
#editor .sugg b { color:var(--fg); font-weight:500; }
#editor .sugg button { padding:2px 8px; font-size:12px; }
#editor .note { color:var(--muted); font-size:12px; }
#editbar { position:fixed; left:12px; top:10px; z-index:10; display:none; align-items:center; gap:6px; padding:6px 10px; border-radius:999px;
  background:var(--box); border:1px solid var(--box-border); box-shadow:var(--shadow); font-size:13px; }
#editbar.show { display:flex; }
#editbar button { border:0; border-radius:999px; padding:5px 10px; cursor:pointer; background:var(--btn); color:var(--fg); font:500 13px/1 system-ui, sans-serif; }
#editbar button.primary { background:var(--root-bg); color:#fff; }
#editbar button:disabled { opacity:.45; cursor:not-allowed; }
#editbar .errs { color:#c23b3b; max-width:50vw; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
#editbar .count { font-weight:600; }
#menu { position:fixed; z-index:30; display:none; min-width:200px; background:var(--box); color:var(--fg); border:1px solid var(--box-border);
  border-radius:8px; box-shadow:0 6px 24px rgba(0,0,0,.25); padding:4px; font-size:13px; }
#menu div { padding:6px 10px; border-radius:6px; cursor:pointer; white-space:nowrap; }
#menu div:hover { background:var(--btn); }
#menu .sep { height:1px; padding:0; margin:4px 6px; background:var(--box-border); cursor:default; }
#panel { position:fixed; top:0; right:0; bottom:0; width:min(460px, 90vw); z-index:20; display:none; flex-direction:column; gap:8px;
  background:var(--box); color:var(--fg); border-left:1px solid var(--box-border); box-shadow:-6px 0 24px rgba(0,0,0,.2); padding:14px; }
#panel.show { display:flex; }
#panel h3 { margin:0; font-size:14px; padding-right:60px; }
#panel textarea { flex:1; width:100%; box-sizing:border-box; resize:none; border:1px solid var(--box-border); border-radius:8px; padding:10px;
  background:var(--bg); color:var(--fg); font:13px/1.5 ui-monospace, "Cascadia Mono", Consolas, "D2Coding", monospace; white-space:pre-wrap; }
#panel .row { display:flex; gap:6px; }
#panel button { border:0; border-radius:999px; padding:6px 12px; cursor:pointer; background:var(--btn); color:var(--fg); font:500 13px/1 system-ui, sans-serif; }
#panel .close { position:absolute; top:10px; right:12px; }
/* 시나리오 계산기 (설계 v6) — 서버 /preview 식 하나로 계산한다 */
#calc { position:fixed; top:0; right:0; bottom:0; width:min(680px, 96vw); z-index:26; display:none; flex-direction:column; gap:8px; overflow:auto;
  background:var(--box); color:var(--fg); border-left:1px solid var(--box-border); box-shadow:-6px 0 24px rgba(0,0,0,.2); padding:14px; font-size:13px; }
#calc.show { display:flex; }
#calc h3 { margin:0; font-size:14px; padding-right:60px; }
#calc .close { position:absolute; top:10px; right:12px; }
#calc table { border-collapse:collapse; width:100%; font-size:12.5px; }
#calc th, #calc td { border-bottom:1px solid var(--box-border); padding:4px 6px; text-align:right; vertical-align:middle; white-space:nowrap; }
#calc th:first-child, #calc td:first-child { text-align:left; white-space:normal; }
#calc th { color:var(--muted); font-weight:500; }
#calc input, #calc select { font:12.5px/1.3 inherit; color:var(--fg); background:var(--bg); border:1px solid var(--box-border); border-radius:6px; padding:3px 6px; width:84px; box-sizing:border-box; text-align:right; }
#calc select { width:auto; text-align:left; }
#calc input.wide { width:100%; text-align:left; }
#calc .row { display:flex; gap:6px; flex-wrap:wrap; align-items:center; }
#calc button { border:0; border-radius:999px; padding:6px 12px; cursor:pointer; background:var(--btn); color:var(--fg); font:500 13px/1 system-ui, sans-serif; }
#calc button.primary { background:var(--root-bg); color:#fff; }
#calc button:disabled { opacity:.45; cursor:not-allowed; }
#calc .note { color:var(--muted); font-size:12px; }
#calc .res { background:var(--code-bg); border-radius:8px; padding:8px 10px; white-space:pre-wrap; font-size:12.5px; }
#calc .warn { color:#c23b3b; }
#calc .src { color:var(--muted); font-size:11px; }
#calc fieldset { border:1px solid var(--box-border); border-radius:8px; padding:8px 10px; margin:0; display:flex; flex-direction:column; gap:6px; }
#calc legend { font-size:12px; color:var(--muted); padding:0 4px; }
#toast { position:fixed; left:50%; bottom:40px; transform:translateX(-50%); z-index:40; display:none; padding:8px 14px; border-radius:999px;
  background:var(--root-bg); color:#fff; font-size:13px; box-shadow:0 4px 16px rgba(0,0,0,.25); }
/* 절대 배치 박스는 담는 상자 폭이 0 이면 한 글자씩 줄바꿈된다 — width:max-content 가 그것을 막는다 */
.node.root { background:var(--root-bg); color:var(--root-fg); border-color:var(--root-bg); font-size:16px; padding:10px 16px; max-width:460px; }
.node.depth1 { font-weight:600; }
.node sub { font-size:.78em; color:var(--muted); vertical-align:baseline; margin-left:.25em; }
.node.root sub { color:rgba(255,255,255,.75); }
.node i { color:var(--muted); }
.node code { font-size:.9em; background:var(--code-bg); padding:0 .3em; border-radius:4px; }
.node .more { display:inline-block; margin-left:.4em; font-size:.75em; color:var(--muted); border:1px solid var(--box-border);
  border-radius:999px; padding:0 .45em; }
.node.root .more { color:rgba(255,255,255,.85); border-color:rgba(255,255,255,.5); }
#bar { position:fixed; top:10px; right:12px; z-index:10; display:flex; gap:6px; }
#bar button, #bar select { border:0; border-radius:999px; padding:6px 12px; cursor:pointer; background:var(--btn); color:var(--fg);
  font:500 13px/1 system-ui, sans-serif; opacity:.9; }
#bar button:hover { opacity:1; }
#hint { position:fixed; left:12px; bottom:10px; color:var(--muted); font-size:12px; }
"""

_JS = r"""
(function () {
  const DATA = __DATA__;
  const OPTS = __OPTS__;
  const COLORS = ["#2f6fb3", "#d9822b", "#3b9a5a", "#c23b3b", "#7b5cc2", "#1f9aa8", "#b4a023", "#c8578b"];
  const GAP_X = 44, GAP_Y = 10, RADIAL_STEP = 230;
  const stage = document.getElementById("stage"), vp = document.getElementById("viewport");
  const svg = document.getElementById("edges"), layer = document.getElementById("nodes");
  const root = document.documentElement, key = "kstock-mindmap-theme";
  let layout = OPTS.layout || "balanced", tx = 0, ty = 0, k = 1;
  const EDIT = OPTS.edit || null;  // 편집 설정 (요약 뷰에서는 null)
  const nodes = new Map();         // id -> {d, el, open, w, h, x, y, color, depth}
  let TREE = DATA;                 // 지금 그리는 트리 (편집을 적용한 것). DATA 는 원본.
  let selectedId = null;

  // ---------- 트리 준비 (편집 뒤 다시 부를 수 있다 — 접힘 상태는 id 로 이어받는다)
  function loadTree(tree) {
    const wasOpen = new Map(); for (const [id, n] of nodes) wasOpen.set(id, n.open);
    nodes.clear(); layer.innerHTML = "";
    (function walk(d, depth, color) {
      const open = wasOpen.has(d.id) ? wasOpen.get(d.id) : (!d.fold && (OPTS.expandLevel == null || depth < OPTS.expandLevel || depth === 0));
      nodes.set(d.id, { d, open, depth, color, children: d.children.map(c => c.id) });
      d.children.forEach((c, i) => walk(c, depth + 1, depth === 0 ? COLORS[i % COLORS.length] : color));
    })(tree, 0, null);
    TREE = tree;
    if (selectedId && !nodes.has(selectedId)) selectedId = null;
  }
  loadTree(DATA);

  function descendants(id) { const n = nodes.get(id); return n.children.reduce((s, c) => s + 1 + descendants(c), 0); }

  // ---------- DOM 노드 만들기 / 재기
  function ensure(id) {
    const n = nodes.get(id);
    if (!n.el) {
      const el = document.createElement("div");
      el.className = "node";
      el.dataset.id = id;
      if (n.depth === 0) el.classList.add("root");
      if (n.depth === 1) el.classList.add("depth1");
      if (n.children.length) el.classList.add("has-kids");
      el.style.borderLeftColor = n.color || "var(--root-bg)";
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        if (e.target.classList.contains("copy")) { copyText(n.d.text, "한 줄 복사"); return; }
        if (e.target.classList.contains("pen")) { openEditor(id); return; }
        select(id);
        // 글자를 긁어 선택한 뒤의 클릭은 접기/펼치기가 아니다 — 복사하려는 것
        const sel = window.getSelection && window.getSelection();
        if (sel && sel.toString().trim() && el.contains(sel.anchorNode)) return;
        if (n.children.length) { n.open = !n.open; render(); }
      });
      el.addEventListener("dblclick", (e) => { e.stopPropagation(); e.preventDefault(); if (EDIT && !n.d.locked) openEditor(id); else openPanel(id); });
      el.addEventListener("contextmenu", (e) => { e.preventDefault(); e.stopPropagation(); select(id); openMenu(id, e.clientX, e.clientY); });
      layer.appendChild(el);
      n.el = el;
    }
    const more = (!n.open && n.children.length) ? `<span class="more">+${descendants(id)}</span>` : "";
    const pen = (EDIT && !n.d.locked) ? `<span class="pen" title="편집 (더블클릭)">✎</span>` : "";
    const mark = n.d.added ? `<span class="mark" title="추가됨 (저장 전)">＋</span>` : (n.d.edited ? `<span class="mark" title="고침 (저장 전)">✎</span>` : "");
    n.el.innerHTML = mark + n.d.html + more + pen + `<span class="copy" title="한 줄 복사 (우클릭: 더 보기)">⧉</span>`;
    n.el.classList.toggle("selected", selectedId === id);
    n.el.classList.toggle("edited", !!n.d.edited && !n.d.added);
    n.el.classList.toggle("added", !!n.d.added);
    n.el.style.display = "";
    n.w = n.el.offsetWidth; n.h = n.el.offsetHeight;
    return n;
  }

  // ---------- 레이아웃
  function span(n) {
    if (!n.open || !n.children.length) return n.h + GAP_Y;
    return Math.max(n.h + GAP_Y, n.children.reduce((s, c) => s + span(nodes.get(c)), 0));
  }
  function place(n, dir) {
    if (!n.open) return;
    const kids = n.children.map(c => nodes.get(c));
    const total = kids.reduce((s, c) => s + span(c), 0);
    let cur = n.y - total / 2;
    for (const c of kids) {
      const sp = span(c);
      c.y = cur + sp / 2;
      c.x = n.x + dir * (n.w / 2 + GAP_X + c.w / 2);
      c.dir = dir;
      cur += sp;
      place(c, dir);
    }
  }
  function layoutSides(r, sides) {
    r.x = 0; r.y = 0; r.dir = 0;
    for (const [dir, ids] of sides) {
      const kids = ids.map(c => nodes.get(c));
      const total = kids.reduce((s, c) => s + span(c), 0);
      let cur = -total / 2;
      for (const c of kids) {
        const sp = span(c);
        c.y = cur + sp / 2;
        c.x = dir * (r.w / 2 + GAP_X + c.w / 2);
        c.dir = dir;
        cur += sp;
        place(c, dir);
      }
    }
  }
  function layoutBalanced(r) {
    // 큰 가지부터, 현재 세로 길이가 짧은 쪽에 배정 → 좌우 균형
    const kids = r.children.map(c => nodes.get(c)).sort((a, b) => span(b) - span(a));
    const right = [], left = []; let hr = 0, hl = 0;
    for (const c of kids) { if (hr <= hl) { right.push(c.d.id); hr += span(c); } else { left.push(c.d.id); hl += span(c); } }
    // 화면 순서는 원래 순서대로
    const order = r.children;
    right.sort((a, b) => order.indexOf(a) - order.indexOf(b)); left.sort((a, b) => order.indexOf(a) - order.indexOf(b));
    layoutSides(r, [[1, right], [-1, left]]);
  }
  function layoutRight(r) { layoutSides(r, [[1, r.children]]); }
  function layoutRadial(r) {
    // 2단계: ① span 비례로 각도를 임시 배정 → ② 각 노드가 그 각도에서 차지하는 호 길이(박스의 반지름 수직 투영)로
    // 각도를 다시 나누고, 각 깊이의 반지름은 모든 부모의 부채꼴에 자식이 들어가도록 키운다.
    r.x = 0; r.y = 0; r.dir = 0;
    const need = n => (n.w * Math.abs(Math.sin(n.a)) + n.h * Math.abs(Math.cos(n.a))) + GAP_Y;  // 반지름에 수직인 폭
    const assign = (n, a0, a1, weight) => {
      if (!n.open) return;
      const kids = n.children.map(c => nodes.get(c)), total = kids.reduce((s, c) => s + weight(c), 0) || 1;
      let a = a0;
      for (const c of kids) {
        const w = (a1 - a0) * (weight(c) / total);
        c.a = a + w / 2; c.a0 = a; c.a1 = a + w;
        assign(c, a, a + w, weight);
        a += w;
      }
    };
    r.a = 0; r.a0 = -Math.PI / 2; r.a1 = Math.PI * 1.5;
    assign(r, r.a0, r.a1, span);                                   // ① 임시 각도
    const wsum = n => (!n.open || !n.children.length) ? need(n) : Math.max(need(n), n.children.reduce((s, c) => s + wsum(nodes.get(c)), 0));
    assign(r, r.a0, r.a1, wsum);                                   // ② 실제 필요 폭으로 재배정
    const radius = [0];
    (function depthReq(n, d, req) {
      if (!n.open) return;
      const kids = n.children.map(c => nodes.get(c));
      const total = kids.reduce((s, c) => s + need(c), 0), range = Math.max(1e-6, n.a1 - n.a0);
      req[d] = Math.max(req[d] || 0, total / range);
      kids.forEach(c => depthReq(c, d + 1, req));
      return req;
    })(r, 1, radius);
    for (let d = 1; d < radius.length; d++) radius[d] = Math.max((radius[d - 1] || 0) + RADIAL_STEP, (radius[d] || 0) * 1.05);
    (function placeR(n, depth) {
      if (!n.open) return;
      for (const cid of n.children) {
        const c = nodes.get(cid), rr = radius[depth];
        c.x = rr * Math.cos(c.a); c.y = rr * Math.sin(c.a); c.dir = Math.cos(c.a) >= 0 ? 1 : -1;
        placeR(c, depth + 1);
      }
    })(r, 1);
  }

  // ---------- 그리기
  function visible(id, out) { const n = nodes.get(id); out.push(n); if (n.open) n.children.forEach(c => visible(c, out)); return out; }
  function edgePath(p, c) {
    if (layout === "radial") {
      return `M${p.x},${p.y} Q${(p.x + c.x) / 2},${(p.y + c.y) / 2} ${c.x},${c.y}`;
    }
    const dir = c.dir || 1;
    const x0 = p.x + dir * p.w / 2, y0 = p.y, x1 = c.x - dir * c.w / 2, y1 = c.y, mx = (x0 + x1) / 2;
    return `M${x0},${y0} C${mx},${y0} ${mx},${y1} ${x1},${y1}`;
  }
  function render() {
    // 숨김 노드 정리 후 보이는 노드 재기
    for (const n of nodes.values()) if (n.el) n.el.style.display = "none";
    const vis = visible(TREE.id, []);
    vis.forEach(n => ensure(n.d.id));
    const r = nodes.get(TREE.id);
    if (layout === "radial") layoutRadial(r); else if (layout === "right") layoutRight(r); else layoutBalanced(r);
    // 경계
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of vis) { minX = Math.min(minX, n.x - n.w / 2); maxX = Math.max(maxX, n.x + n.w / 2); minY = Math.min(minY, n.y - n.h / 2); maxY = Math.max(maxY, n.y + n.h / 2); }
    const pad = 40; minX -= pad; minY -= pad; maxX += pad; maxY += pad;
    svg.setAttribute("width", maxX - minX); svg.setAttribute("height", maxY - minY);
    svg.style.left = minX + "px"; svg.style.top = minY + "px";
    svg.setAttribute("viewBox", `${minX} ${minY} ${maxX - minX} ${maxY - minY}`);
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    for (const n of vis) {
      n.el.style.left = (n.x - n.w / 2) + "px"; n.el.style.top = (n.y - n.h / 2) + "px";
      if (!n.open) continue;
      for (const cid of n.children) {
        const c = nodes.get(cid);
        const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
        p.setAttribute("d", edgePath(n, c)); p.setAttribute("stroke", c.color || "#888");
        svg.appendChild(p);
      }
    }
    render.bounds = { minX, minY, maxX, maxY };
    applyTransform();
  }
  function applyTransform() { vp.style.transform = `translate(${tx}px, ${ty}px) scale(${k})`; }
  function fit() {
    const b = render.bounds; if (!b) return;
    const W = stage.clientWidth, H = stage.clientHeight;
    k = Math.min(1.25, Math.min(W / (b.maxX - b.minX), H / (b.maxY - b.minY)));
    tx = W / 2 - ((b.minX + b.maxX) / 2) * k; ty = H / 2 - ((b.minY + b.maxY) / 2) * k;
    applyTransform();
  }

  // ---------- 복사 · 상세 (노드 글자는 그대로 긁어 복사할 수 있고, ⧉ / 우클릭 / Ctrl+C 도 된다)
  const menu = document.getElementById("menu"), panel = document.getElementById("panel"), toast = document.getElementById("toast");
  function select(id) {
    if (selectedId && nodes.get(selectedId) && nodes.get(selectedId).el) nodes.get(selectedId).el.classList.remove("selected");
    selectedId = id;
    const n = nodes.get(id); if (n && n.el) n.el.classList.add("selected");
  }
  function showToast(msg) { toast.textContent = msg; toast.style.display = "block"; clearTimeout(showToast.t); showToast.t = setTimeout(() => toast.style.display = "none", 1400); }
  function copyText(text, what) {
    const done = () => showToast((what || "복사") + " — " + (text.length > 40 ? text.slice(0, 40) + "…" : text));
    const fallback = () => {
      const ta = document.createElement("textarea"); ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.focus(); ta.select();
      try { document.execCommand("copy"); done(); } catch (e) { showToast("복사 실패 — 상세 패널에서 직접 선택해 복사하세요"); }
      document.body.removeChild(ta);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, fallback); else fallback();
  }
  function subtreeMarkdown(id, depth) {
    const n = nodes.get(id);
    let out = "  ".repeat(depth) + "- " + n.d.text + "\n";
    n.children.forEach(c => { out += subtreeMarkdown(c, depth + 1); });
    return out;
  }
  function openMenu(id, x, y) {
    const n = nodes.get(id);
    menu.dataset.id = id; menu.style.display = "block";
    for (const item of menu.querySelectorAll("[data-edit]")) item.style.display = EDIT ? "" : "none";
    const lockedItem = menu.querySelector('[data-act="edit"]'), delItem = menu.querySelector('[data-act="delete"]');
    if (lockedItem) lockedItem.style.display = (EDIT && !n.d.locked) ? "" : "none";
    if (delItem) delItem.style.display = (EDIT && !n.d.locked && n.depth > 0) ? "" : "none";
    const W = window.innerWidth, H = window.innerHeight;
    menu.style.left = Math.min(x, W - menu.offsetWidth - 8) + "px"; menu.style.top = Math.min(y, H - menu.offsetHeight - 8) + "px";
  }
  function closeMenu() { menu.style.display = "none"; }
  menu.addEventListener("click", (e) => {
    const act = e.target.dataset.act, id = menu.dataset.id; if (!act || !id) return;
    const n = nodes.get(id); closeMenu();
    if (act === "line") copyText(n.d.text, "한 줄 복사");
    else if (act === "detail") copyText(n.d.detail, "상세 복사");
    else if (act === "tree") copyText(subtreeMarkdown(id, 0), "가지 복사 (Markdown)");
    else if (act === "id") copyText(id, "id 복사");
    else if (act === "panel") openPanel(id);
    else if (act === "edit") openEditor(id);
    else if (act === "addchild") openAdder(id);
    else if (act === "delete") askDelete(id);
    else if (act === "calc") openCalc();
  });
  function openPanel(id) {
    const n = nodes.get(id);
    document.getElementById("panel-title").textContent = n.d.text.length > 80 ? n.d.text.slice(0, 80) + "…" : n.d.text;
    document.getElementById("panel-text").value = n.d.detail;
    panel.dataset.id = id; panel.classList.add("show");
    const ta = document.getElementById("panel-text"); ta.focus(); ta.select();
  }
  document.getElementById("panel-close").addEventListener("click", () => panel.classList.remove("show"));
  document.getElementById("panel-copy").addEventListener("click", () => copyText(document.getElementById("panel-text").value, "상세 복사"));
  document.getElementById("panel-line").addEventListener("click", () => copyText(nodes.get(panel.dataset.id).d.text, "한 줄 복사"));
  document.getElementById("panel-tree").addEventListener("click", () => copyText(subtreeMarkdown(panel.dataset.id, 0), "가지 복사 (Markdown)"));
  document.getElementById("copyall").addEventListener("click", () => copyText(subtreeMarkdown(TREE.id, 0), "전체 복사 (Markdown)"));
  window.addEventListener("click", closeMenu);
  window.addEventListener("keydown", e => {
    if (e.key === "Escape") { closeMenu(); panel.classList.remove("show"); return; }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c") {
      // 글자를 선택한 상태면 브라우저 기본 복사. 아니면 고른 노드의 한 줄 (Shift 를 누르면 상세)
      const sel = window.getSelection && window.getSelection();
      if (sel && sel.toString().trim()) return;
      if (document.activeElement && document.activeElement.tagName === "TEXTAREA") return;
      if (selectedId) { e.preventDefault(); const n = nodes.get(selectedId); copyText(e.shiftKey ? n.d.detail : n.d.text, e.shiftKey ? "상세 복사" : "한 줄 복사"); }
    }
  });

  // ---------- 편집 (generate/edits.py 의 연산 목록을 쌓는다 — 서버가 있으면 미리보기·저장, 없으면 edits.json 내려받기)
  const SCHEMA = EDIT ? EDIT.schemas : null;
  const SUBS = ["driver", "scenario", "rule", "event"];
  const edits = [];
  let tempSeq = 0, pendingDelete = null, lastErrors = [], savedLen = 0;
  const editor = document.getElementById("editor"), editbar = document.getElementById("editbar"), eform = document.getElementById("eform");
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clone = o => JSON.parse(JSON.stringify(o));
  function findIn(tree, id) { if (tree.id === id) return tree; for (const c of tree.children) { const f = findIn(c, id); if (f) return f; } return null; }
  function parentIn(tree, id, p) { if (tree.id === id) return p; for (const c of tree.children) { const f = parentIn(c, id, tree); if (f) return f; } return null; }
  function fmtNum(v) { return (typeof v === "number") ? (Number.isInteger(v) ? v.toLocaleString("en-US") : String(v)) : String(v); }
  function localText(ed) {
    const label = ed.label || "", sub = ed.sub; let val = "";
    if (sub && sub.kind === "driver") {
      const d = sub.data || {}, u = ed.unit || "", seq = [];
      if (d.at_entry != null) seq.push("매수시 " + fmtNum(d.at_entry) + u);
      if (d.current && d.current.value != null) seq.push("현재 " + fmtNum(d.current.value) + u);
      if (d.consensus && d.consensus.value != null) seq.push("컨센 " + fmtNum(d.consensus.value) + u);
      if (d.my_estimate && d.my_estimate.value != null) seq.push("내 추정 " + fmtNum(d.my_estimate.value) + u);
      val = seq.join(" → ") + (d.next_check ? " · 확인 " + d.next_check : "");
    } else if (sub && sub.kind === "scenario") {
      const s = sub.data || {};
      val = (s.name ? s.name + ": " : "") + (s.target_price != null ? fmtNum(s.target_price) + "원" : "미계산") + (s.probability != null ? " (p=" + s.probability + ", " + (s.horizon_months || 12) + "M)" : "")
        + (s.price_source === "computed" ? " — 가정 " + ((s.driver_assumptions || []).filter(d => d.delta_pct != null).map(d => d.node_id + " " + (d.delta_pct >= 0 ? "+" : "") + d.delta_pct + (d.unit || "%")).join(" · ") || "없음(컨센 경로)") + (s.per ? " · PER " + s.per : "") + " (저장 뒤 계산)"
           : (s.assumptions && s.assumptions.length ? " — " + s.assumptions.join("; ") : ""));
    } else if (sub && sub.kind === "rule") { const r = sub.data || {}; val = "⚡ " + (r.condition || "") + " → " + (r.action || ""); }
    else if (sub && sub.kind === "event") { const e = sub.data || {}; val = (e.date || "") + " " + (e.fact || ""); }
    else if (ed.value != null && ed.value !== "") val = (typeof ed.value === "object") ? JSON.stringify(ed.value) : (typeof ed.value === "number" ? fmtNum(ed.value) + (ed.unit || "") : String(ed.value));
    return val ? label + ": " + val : label;
  }
  function flat(v, ind, L) {
    if (v == null) return;
    if (Array.isArray(v)) { for (const x of v) { if (x && typeof x === "object") flat(x, ind, L); else L.push(ind + "- " + x); } }
    else if (typeof v === "object") { for (const [k, x] of Object.entries(v)) { if (x == null || x === "" || (Array.isArray(x) && !x.length)) continue; if (typeof x === "object") { L.push(ind + k + ":"); flat(x, ind + "  ", L); } else L.push(ind + k + ": " + x); } }
    else L.push(ind + v);
  }
  function localDetail(id, ed) {
    const L = ["[" + id + "] " + (ed.label || "")];
    if (ed.sub) { L.push((SCHEMA.sub_labels[ed.sub.kind] || ed.sub.kind) + ":"); flat(ed.sub.data, "  ", L); }
    else if (ed.value != null && ed.value !== "") { L.push("값:"); if (typeof ed.value === "object") flat(ed.value, "  ", L); else L.push("  " + ed.value); }
    if (ed.question) L.push("질문: " + ed.question);
    if (ed.source && (ed.source.name || ed.source.url)) L.push("출처: " + [ed.source.name, ed.source.url].filter(Boolean).join(" "));
    if (ed.as_of) L.push("기준일: " + ed.as_of);
    if (ed.status) L.push("상태: " + ed.status);
    if (ed.tags && ed.tags.length) L.push("태그: " + ed.tags.join(", "));
    if (ed.links && ed.links.length) L.push("연결: " + ed.links.join(", "));
    return L.join("\n");
  }
  function refreshLocal(d) { d.text = localText(d.edit); d.html = esc(d.text); d.detail = localDetail(d.id, d.edit); d.kind = d.edit.sub ? d.edit.sub.kind : d.edit.node_type; }
  function blankEdit() {
    return { label: "", question: "", value: null, value_type: "text", unit: null, node_type: "estimate", enum_options: null, source: null, as_of: EDIT ? EDIT.today : null,
             confidence: null, status: "unverified", next_check: null, weight: null, links: [], tags: ["user"], formula: null, sub: null, suggest: [] };
  }
  function assignFields(ed, fields) {
    for (const [k, v] of Object.entries(fields)) {
      if (k === "_accepted") continue;
      if (SUBS.includes(k)) ed.sub = v ? { kind: k, data: clone(v) } : null; else ed[k] = clone(v);
    }
  }
  function localApply(tree, ops) {
    // 서버 없이 보는 임시 표시 — 저장 뒤 다시 렌더링하면 정확한 표시(node_line) 가 된다
    const t = clone(tree);
    for (const op of ops) {
      if (op.op === "set") { const d = findIn(t, op.id); if (!d) continue; assignFields(d.edit, op.fields); refreshLocal(d); d.edited = true; }
      else if (op.op === "add") {
        const p = findIn(t, op.parent); if (!p) continue;
        const ed = blankEdit(); assignFields(ed, op.fields);
        const d = { id: op.id, fold: false, children: [], edit: ed, added: true }; refreshLocal(d); p.children.push(d);
      } else if (op.op === "delete") { const p = parentIn(t, op.id, null); if (p) p.children = p.children.filter(c => c.id !== op.id); }
    }
    return t;
  }
  function markEdited(tree, idMap) {
    for (const op of edits) {
      if (op.op === "set") { const d = findIn(tree, idMap[op.id] || op.id); if (d && !d.added) d.edited = true; }
      else if (op.op === "add") { const d = findIn(tree, idMap[op.id] || op.id); if (d) d.added = true; }
    }
  }
  async function api(path, body) {
    const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Token": EDIT.token }, body: JSON.stringify(body) });
    return await res.json();
  }
  async function refresh() {
    // 편집 목록 전체를 원본에 다시 적용한다. 실패(서버가 거절) 면 null 대신 오류 문자열을 돌려준다.
    let tree, errs = [], warns = [];
    if (!edits.length) tree = DATA;
    else if (EDIT.server) {
      let r;
      try { r = await api("/preview", { edits }); } catch (e) { return "편집 서버에 닿지 않는다 — " + e; }
      if (!r.ok) return r.error || "적용 실패";
      tree = r.tree; errs = r.new_errors || []; warns = r.warnings || []; markEdited(tree, r.id_map || {});
    } else tree = localApply(DATA, edits);
    loadTree(tree); render(); updateBar(errs, warns);
    return null;
  }
  async function commit(op) {
    edits.push(op);
    const err = await refresh();
    if (err) { edits.pop(); await refresh(); }
    return err;
  }
  async function undo() { if (!edits.length) return; edits.pop(); await refresh(); showToast("되돌림 — 남은 편집 " + edits.length + "건"); }
  async function discard() { edits.length = 0; await refresh(); closeEditor(); showToast("편집을 모두 취소했다"); }
  function updateBar(errs, warns) {
    lastErrors = errs || [];
    editbar.classList.toggle("show", edits.length > 0);
    editbar.querySelector(".count").textContent = "고침 " + edits.length + "건";
    const e = editbar.querySelector(".errs");
    const msg = (lastErrors.length ? "⚠ 저장하면 검증 오류: " + lastErrors.join(" · ") : "") + (warns && warns.length ? " ⚑ " + warns.join(" · ") : "");
    e.textContent = msg; e.title = msg; e.style.display = msg ? "" : "none";
    editbar.querySelector("#ed-save").disabled = lastErrors.length > 0;
  }
  async function save() {
    if (!edits.length) { showToast("고친 것이 없다"); return; }
    if (lastErrors.length) { showToast("검증 오류가 있어 저장할 수 없다 — 편집을 되돌리거나 고칠 것"); return; }
    const payload = { map_id: EDIT.map_id, base_hash: EDIT.base_hash, created_at: new Date().toISOString(), edits };
    if (EDIT.server) {
      let r;
      try { r = await api("/save", payload); } catch (e) { showToast("저장 실패 — " + e); return; }
      if (r.ok) { edits.length = 0; updateBar([], []); showToast("저장됨 — v" + r.version + " · " + (r.applied || []).length + "건"); setTimeout(() => location.reload(), 700); }
      else showToast("저장 거절 — " + r.error);
    } else {
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = EDIT.map_id.replace(/:/g, "_") + ".edits.json";
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      savedLen = edits.length;
      showToast("편집 파일을 내려받았다 — `mindmap apply-edits <파일>` 또는 콘솔 「편집 파일 반영」 으로 맵에 넣는다");
    }
  }
  async function askDelete(id) {
    const n = nodes.get(id); if (!n || n.d.locked || n.depth === 0) return;
    const count = 1 + descendants(id);
    if (pendingDelete !== id) { pendingDelete = id; showToast("삭제: " + id + (count > 1 ? " (가지 " + count + "개 포함)" : "") + " — 한 번 더 누르면 지운다"); setTimeout(() => { if (pendingDelete === id) pendingDelete = null; }, 4000); return; }
    pendingDelete = null; closeEditor();
    const err = await commit({ op: "delete", id });
    showToast(err ? "삭제 거절 — " + err : "삭제했다 (저장 전) — " + id);
  }
  // ----- 편집 폼
  function jsonTemplate(ed) {
    // 질문에 "{suggested, confirmed, reason}" 처럼 구조가 적혀 있고 값이 비어 있으면 그 키로 JSON 틀을 준다
    const m = /\{\s*([a-z_]+(?:\s*,\s*[a-z_]+)+)\s*\}/.exec(ed.question || "");
    if (!m) return null;
    const o = {}; m[1].split(",").forEach(k => { o[k.trim()] = null; }); return o;
  }
  function valueType(ed) {
    if (ed.value != null && typeof ed.value === "object") return "json";
    if (ed.value == null && jsonTemplate(ed)) return "json";
    const vt = ed.value_type || "text";
    if (["number", "percent", "price", "probability"].includes(vt)) return "number";
    if (vt === "date") return "date";
    if (vt === "bool") return "bool";
    if (vt === "enum" && ed.enum_options && ed.enum_options.length) return "enum";
    return "text";
  }
  function inputHtml(name, type, value, opts) {
    const v = value == null ? "" : value;
    if (type === "bool") return `<select name="${name}" data-type="bool"><option value="true"${v === true ? " selected" : ""}>예</option><option value="false"${v === false || v === "" ? " selected" : ""}>아니오</option></select>`;
    if (type === "select" || type === "enum") return `<select name="${name}" data-type="select"><option value=""${v === "" ? " selected" : ""}>—</option>` + (opts || []).map(o => `<option value="${esc(o)}"${String(v) === String(o) ? " selected" : ""}>${esc(o)}</option>`).join("") + `</select>`;
    if (type === "number") return `<input name="${name}" data-type="number" type="number" step="any" value="${esc(v)}">`;
    if (type === "date") return `<input name="${name}" data-type="date" type="date" value="${esc(v)}">`;
    if (type === "list") return `<textarea name="${name}" data-type="list" rows="2" placeholder="줄마다 하나">${esc(Array.isArray(v) ? v.join("\n") : v)}</textarea>`;
    if (type === "json") return `<textarea name="${name}" data-type="json" class="json" rows="5">${esc(v === "" ? "" : JSON.stringify(v, null, 2))}</textarea>`;
    if (type === "textarea") return `<textarea name="${name}" data-type="text" rows="3">${esc(v)}</textarea>`;
    return `<input name="${name}" data-type="text" type="text" value="${esc(v)}">`;
  }
  function getPath(o, path) { return path.split(".").reduce((a, k) => (a == null ? undefined : a[k]), o); }
  function setPath(o, path, v) { const ks = path.split("."); let a = o; for (let i = 0; i < ks.length - 1; i++) { if (a[ks[i]] == null || typeof a[ks[i]] !== "object") a[ks[i]] = {}; a = a[ks[i]]; } a[ks[ks.length - 1]] = v; }
  function subFieldset(kind, data) {
    const rows = SCHEMA.subs[kind] || [], basic = [], adv = [];
    for (const f of rows) {
      const h = `<label>${esc(f.label)}${inputHtml("sub:" + f.key, f.type === "text" && /condition|fact|rationale|note/.test(f.key) ? "textarea" : f.type, getPath(data, f.key), f.options)}</label>`;
      (f.adv ? adv : basic).push(h);
    }
    return `<fieldset data-kind="${kind}"><legend>${esc(SCHEMA.sub_labels[kind] || kind)}</legend>${basic.join("")}` +
      (adv.length ? `<button type="button" class="advtoggle">세부 필드 (기준일·출처·근거) 보기/숨기기</button><div class="adv">${adv.join("")}</div>` : "") + `</fieldset>`;
  }
  function valueBlock(ed) {
    const vt = valueType(ed);
    const opts = vt === "enum" ? ed.enum_options : null;
    const typeSel = `<label>값 형식${inputHtml("value_type", "select", ed.value_type || "text", SCHEMA.enums.value_type)}</label>`;
    const unit = `<label>단위${inputHtml("unit", "text", ed.unit)}</label>`;
    const v0 = (vt === "json" && ed.value == null) ? jsonTemplate(ed) : ed.value;
    const valueIn = `<label>값${vt === "json" ? " (JSON — 질문에 적힌 키 구조)" : ""}${vt === "text" ? inputHtml("value", "textarea", ed.value) : inputHtml("value", vt, v0, opts)}</label>`;
    return `<fieldset data-kind="value"><legend>값</legend>${valueIn}<div class="row two">${typeSel}${unit}</div></fieldset>`;
  }
  // ----- 리포트 기반 제안값 (설계 v4 §6): 빈 칸 옆에 근거와 「채우기」. 받아들인 것은 _accepted 로 보내 history 출처가 된다
  const accepted = new Map();   // 입력 칸 이름 → {field, kind, report_ids, value}
  function inputNameOf(field) { if (field === "value") return "value"; const m = /^(driver|scenario|rule|event)\.(.+)$/.exec(field || ""); return m ? "sub:" + m[2] : null; }
  function suggText(v) { return (v != null && typeof v === "object") ? JSON.stringify(v, null, 2) : (v == null ? "" : String(v)); }
  function renderSuggestions(ed) {
    accepted.clear();
    (ed.suggest || []).forEach((s, i) => {
      const name = inputNameOf(s.field); if (!name) return;
      const el = eform.querySelector(`[name="${CSS.escape(name)}"]`); if (!el || !el.closest("label")) return;
      const box = document.createElement("div"); box.className = "sugg";
      box.innerHTML = `<span>제안: <b>${esc(s.label)}</b>${s.accepted ? " · 이미 수락" : ""}${s.prefilled ? " · 미리 채움(suggested)" : ""}</span><button type="button" class="fill" data-i="${i}">채우기</button>`;
      el.closest("label").appendChild(box);
    });
  }
  function fillSuggestion(i) {
    const s = (editor._ed.suggest || [])[i]; if (!s) return;
    const name = inputNameOf(s.field), el = eform.querySelector(`[name="${CSS.escape(name)}"]`); if (!el) return;
    el.value = suggText(s.value);
    accepted.set(name, { field: s.field, kind: s.kind, report_ids: s.report_ids || [], value: s.value });
    el.focus();
    showToast("제안값을 넣었다 — 그대로 두면 '리포트 제안 수락' 으로, 고치면 내 값으로 기록된다");
  }
  function acceptedNow() {
    const out = [];
    for (const [name, a] of accepted) { const el = eform.querySelector(`[name="${CSS.escape(name)}"]`); if (el && el.value === suggText(a.value)) out.push(a); }
    return out;
  }
  function buildForm(ctx) {
    const ed = ctx.ed, isNew = ctx.mode === "add", canType = isNew || (ed.tags || []).includes("user");
    const subSel = isNew ? `<label class="inline">하위 스키마 ${inputHtml("subkind", "select", ed.sub ? ed.sub.kind : "", SUBS)} <span class="note">드라이버·시나리오·규칙·이벤트는 값 대신 구조화된 필드를 가진다</span></label>` : "";
    const sub = ed.sub ? subFieldset(ed.sub.kind, ed.sub.data || {}) : valueBlock(ed);
    const src = ed.source || {};
    eform.innerHTML =
      `<label>라벨${inputHtml("label", "text", ed.label)}</label>` + subSel + `<div id="subarea">${sub}</div>` +
      `<label>질문 (이 노드가 답해야 할 것)${inputHtml("question", "textarea", ed.question)}</label>` +
      `<div class="row two"><label>출처 이름${inputHtml("source.name", "text", src.name)}</label><label>출처 URL${inputHtml("source.url", "text", src.url)}</label></div>` +
      `<div class="row two"><label>기준일${inputHtml("as_of", "date", ed.as_of)}</label><label>다음 확인일${inputHtml("next_check", "date", ed.next_check)}</label></div>` +
      `<div class="row two"><label>상태${inputHtml("status", "select", ed.status, SCHEMA.enums.status)}</label><label>확신도${inputHtml("confidence", "select", ed.confidence, SCHEMA.enums.confidence)}</label></div>` +
      `<div class="row two"><label>태그 (쉼표)${inputHtml("tags", "text", (ed.tags || []).join(", "))}</label><label>연결 노드 (쉼표)${inputHtml("links", "text", (ed.links || []).join(", "))}</label></div>` +
      `<div class="row two"><label>노드 종류${canType ? inputHtml("node_type", "select", ed.node_type, SCHEMA.enums.node_type) : `<input type="text" value="${esc(ed.node_type)}" disabled title="템플릿 노드의 종류는 바꾸지 않는다">`}</label><label>가중치 (0~1)${inputHtml("weight", "number", ed.weight)}</label></div>` +
      (ed.node_type === "computed" || ed.formula ? `<label>계산식${inputHtml("formula", "text", ed.formula)}</label>` : "") +
      `<div class="err" id="eerr"></div>`;
    editor.dataset.mode = ctx.mode; editor.dataset.id = ctx.id || ""; editor.dataset.parent = ctx.parent || "";
    editor._orig = clone(ed); editor._ed = ed;
    renderSuggestions(ed);
    document.getElementById("editor-title").textContent = ctx.title;
    document.getElementById("ed-delete").style.display = (ctx.mode === "edit" && nodes.get(ctx.id) && nodes.get(ctx.id).depth > 0) ? "" : "none";
    editor.classList.add("show"); panel.classList.remove("show");
    const first = eform.querySelector('input[name="label"]'); if (first) { first.focus(); if (isNew) first.select(); }
  }
  function openEditor(id) {
    if (!EDIT) return;
    const n = nodes.get(id); if (!n || n.d.locked) { showToast("이 항목은 고칠 수 없다"); return; }
    select(id);
    buildForm({ mode: "edit", id, ed: clone(n.d.edit), title: "편집 — " + (n.d.text.length > 60 ? n.d.text.slice(0, 60) + "…" : n.d.text) });
  }
  function openAdder(parentId) {
    if (!EDIT) return;
    const p = nodes.get(parentId); if (!p) return;
    if (!p.open) { p.open = true; render(); }
    buildForm({ mode: "add", parent: parentId, ed: blankEdit(), title: "새 자식 노드 ← " + (p.d.text.length > 40 ? p.d.text.slice(0, 40) + "…" : p.d.text) });
  }
  function closeEditor() { editor.classList.remove("show"); }
  function readInputs() {
    const out = {}; let bad = null;
    for (const el of eform.querySelectorAll("[name]")) {
      const t = el.dataset.type || "text", raw = el.value; let v;
      try {
        if (t === "bool") v = raw === "true";
        else if (t === "number") { v = raw === "" ? null : Number(raw); if (v !== null && !Number.isFinite(v)) throw new Error("숫자가 아니다"); }
        else if (t === "date") v = raw || null;
        else if (t === "list") v = raw.split(/[\n,]/).map(s => s.trim()).filter(Boolean);
        else if (t === "json") v = raw.trim() ? JSON.parse(raw) : null;
        else if (t === "select") v = raw === "" ? null : raw;
        else v = raw;
      } catch (e) { const lb = el.closest("label"); bad = (lb && lb.firstChild && lb.firstChild.nodeType === 3 ? lb.firstChild.textContent.trim() : el.name) + ": " + e.message; break; }
      out[el.name] = v;
    }
    return { out, bad };
  }
  function collect() {
    const { out, bad } = readInputs(); if (bad) return { error: bad };
    const ed = editor._ed, fields = {};
    const splitList = s => String(s || "").split(",").map(x => x.trim()).filter(Boolean);
    fields.label = out.label; fields.question = out.question || "";
    fields.source = (out["source.name"] || out["source.url"]) ? { name: out["source.name"] || "", url: out["source.url"] || "", retrieved_at: (ed.source && ed.source.retrieved_at) || EDIT.today } : null;
    fields.as_of = out.as_of; fields.next_check = out.next_check; fields.status = out.status || "unverified"; fields.confidence = out.confidence;
    fields.tags = splitList(out.tags); fields.links = splitList(out.links); fields.weight = out.weight;
    if ("node_type" in out) fields.node_type = out.node_type;
    if ("formula" in out) fields.formula = out.formula || null;
    const kindSel = out.subkind !== undefined ? out.subkind : (ed.sub ? ed.sub.kind : null);
    if (kindSel) {
      const data = clone((ed.sub && ed.sub.kind === kindSel && ed.sub.data) || {});
      const origData = (ed.sub && ed.sub.kind === kindSel && ed.sub.data) || {};
      for (const [k, v] of Object.entries(out)) if (k.startsWith("sub:")) {
        const key = k.slice(4);
        // 비운 글 칸: 원래 비어 있던(null) 필드는 null 로 두고, 값이 있던 필드만 "" 로 지운다 (필수 str 필드는 "" 가 맞다)
        setPath(data, key, (v === "" && getPath(origData, key) == null) ? null : v);
      }
      (function fixSources(o) { if (!o || typeof o !== "object") return; for (const [k, v] of Object.entries(o)) { if (v && typeof v === "object" && !Array.isArray(v)) { if ("name" in v && "url" in v && !v.name && !v.url) o[k] = null; else fixSources(v); } } })(data);
      fields[kindSel] = data;
      for (const other of SUBS) if (other !== kindSel && ed.sub && ed.sub.kind === other) fields[other] = null;
    } else {
      if ("value_type" in out) fields.value_type = out.value_type || "text";
      if ("unit" in out) fields.unit = out.unit || null;
      if ("value" in out) fields.value = (out.value === "" ? null : out.value);
      if (ed.sub) fields[ed.sub.kind] = null;
    }
    if (!String(fields.label || "").trim()) return { error: "라벨이 비어 있다" };
    const acc = acceptedNow();
    if (acc.length) fields._accepted = acc;
    return { fields };
  }
  function changedOnly(fields, orig) {
    const o = clone(orig), out = {};
    const before = {};
    for (const k of Object.keys(fields)) {
      if (SUBS.includes(k)) before[k] = (o.sub && o.sub.kind === k) ? o.sub.data : null; else before[k] = o[k];
    }
    for (const [k, v] of Object.entries(fields)) {
      if (k === "_accepted") continue;
      if (JSON.stringify(v == null ? null : v) !== JSON.stringify(before[k] == null ? null : before[k])) out[k] = v;
    }
    if (fields._accepted && Object.keys(out).length) out._accepted = fields._accepted;
    return out;
  }
  async function applyForm() {
    const r = collect(); const errEl = document.getElementById("eerr");
    if (r.error) { errEl.textContent = r.error; return; }
    let op;
    if (editor.dataset.mode === "add") op = { op: "add", parent: editor.dataset.parent, id: "new:" + (++tempSeq), fields: r.fields };
    else {
      const diff = changedOnly(r.fields, editor._orig);
      if (!Object.keys(diff).length) { closeEditor(); showToast("바뀐 것이 없다"); return; }
      op = { op: "set", id: editor.dataset.id, fields: diff };
    }
    const err = await commit(op);
    if (err) { errEl.textContent = err; return; }
    closeEditor(); showToast((op.op === "add" ? "추가했다" : "고쳤다") + " (저장 전) — 「저장」 으로 맵에 반영");
  }
  if (EDIT) {
    eform.addEventListener("click", e => {
      if (e.target.classList.contains("advtoggle")) e.target.closest("fieldset").classList.toggle("showadv");
      else if (e.target.classList.contains("fill")) fillSuggestion(Number(e.target.dataset.i));
    });
    eform.addEventListener("change", e => {
      const ed = editor._ed;
      if (e.target.name === "subkind") {
        const kind = e.target.value; ed.sub = kind ? { kind, data: ed.sub && ed.sub.kind === kind ? ed.sub.data : {} } : null;
        document.getElementById("subarea").innerHTML = ed.sub ? subFieldset(kind, ed.sub.data) : valueBlock(ed);
        renderSuggestions(ed);
      } else if (e.target.name === "value_type" && !ed.sub) {
        const { out } = readInputs(); ed.value_type = e.target.value; ed.value = out.value === "" ? null : out.value; ed.unit = out.unit;
        document.getElementById("subarea").innerHTML = valueBlock(ed);
        renderSuggestions(ed);
      }
    });
    eform.addEventListener("submit", e => { e.preventDefault(); applyForm(); });
    document.getElementById("ed-apply").addEventListener("click", applyForm);
    document.getElementById("ed-cancel").addEventListener("click", closeEditor);
    document.getElementById("editor-close").addEventListener("click", closeEditor);
    document.getElementById("ed-delete").addEventListener("click", () => askDelete(editor.dataset.id));
    document.getElementById("ed-undo").addEventListener("click", undo);
    document.getElementById("ed-save").addEventListener("click", save);
    document.getElementById("ed-discard").addEventListener("click", discard);
    document.getElementById("ed-save").textContent = EDIT.server ? "저장" : "편집 파일 내려받기";
    window.addEventListener("beforeunload", e => { if (edits.length && edits.length !== savedLen) { e.preventDefault(); e.returnValue = ""; } });
    window.addEventListener("keydown", e => {
      const tag = document.activeElement && document.activeElement.tagName, typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
      if (e.key === "Escape") { closeEditor(); return; }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); if (editor.classList.contains("show")) applyForm(); else save(); return; }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !typing) { e.preventDefault(); undo(); return; }
      if (typing) return;
      if (e.key === "F2" && selectedId) { e.preventDefault(); openEditor(selectedId); }
      else if (e.key === "Delete" && selectedId) { e.preventDefault(); askDelete(selectedId); }
      else if (e.key === "Insert" && selectedId) { e.preventDefault(); openAdder(selectedId); }
    });
  }

  // ---------- 밸류에이션 & 시나리오 계산기 (설계 v6) — 가정 묶음을 편집 연산(set A3.8 · set A3.1~3 scenario) 으로 만들고,
  // 계산은 서버 /preview 의 통합 모델이 한다 (클라이언트에 식을 복제하지 않는다 — 단일 진실). 서버가 없으면 「적용」 만 되고 결과는 저장 뒤 CLI 로.
  const calc = document.getElementById("calc"), calcBody = document.getElementById("calc-body"), calcRes = document.getElementById("calc-res");
  const SCEN = ["Bear", "Base", "Bull"], SCEN_ID = { Bear: "A3.1", Base: "A3.2", Bull: "A3.3" };
  const V = EDIT && EDIT.valuation ? EDIT.valuation : null;
  function cur(id) { const n = nodes.get(id); return n ? n.d : null; }
  function calcDrivers() {
    const a1 = cur("A1"); if (!a1) return [];
    return a1.children.filter(d => d && d.edit && d.edit.sub && d.edit.sub.kind === "driver" && d.edit.sub.data.role === "cause")
      .map(d => ({ id: d.id, label: (d.edit.label || d.id).split(" (")[0], kind: d.edit.sub.data.kind, meta: (V && V.drivers && V.drivers[d.id]) || {} }));
  }
  function scenData(name) { const d = cur(SCEN_ID[name]); return d && d.edit.sub && d.edit.sub.kind === "scenario" ? clone(d.edit.sub.data) : null; }
  function critData() { const d = cur("A3.8"); return d && d.edit.value && typeof d.edit.value === "object" ? clone(d.edit.value) : {}; }
  function numOr(v) { return (v == null || v === "") ? null : Number(v); }
  function tplOf(dr) { return dr.meta.template || ({ asp: "price_volume", volume: "price_volume", cost: "cost_ratio", margin: "cost_ratio", op: "direct_op", fcf: "payout", orders: "orders" }[dr.meta.variable] || (dr.kind === "qualitative" ? "cost_ratio" : "price_volume")); }
  function unitOf(tpl) { return tpl === "cost_ratio" ? "%p" : "%"; }
  function buildCalc() {
    const drivers = calcDrivers(), crit = critData();
    const scs = {}; SCEN.forEach(n => { scs[n] = scenData(n); });
    const p0 = V && V.p0 != null ? fmtNum(V.p0) + "원" : "현재가 없음";
    let h = `<div class="note">현재가 ${p0} · 기준 연도 ${esc((V && V.year) || "표 기준")} · 컨센 EPS ${V && V.eps != null ? fmtNum(Math.round(V.eps)) : "-"} · 목표 PER 기본값 ${V && V.per != null ? V.per.toFixed(2) : "-"}` +
      (V && V.line ? `<br>${esc(V.line)}` : "") + `</div>`;
    h += `<table><thead><tr><th>드라이버 (가정은 컨센 경로 대비)</th>` + SCEN.map(n => `<th>${n}${scs[n] && scs[n].price_source !== "computed" && scs[n].target_price != null ? ` <span class="src">(${esc(scs[n].price_source)} ${fmtNum(scs[n].target_price)})</span>` : ""}</th>`).join("") + `</tr></thead><tbody>`;
    for (const dr of drivers) {
      const tpl = tplOf(dr), unit = unitOf(tpl);
      if (tpl === "payout" || tpl === "orders") { h += `<tr><td>${esc(dr.id)} ${esc(dr.label)} <span class="src">${esc(tpl)} — 가격 경로 밖 (주주환원·수주는 민감도로만)</span></td>` + SCEN.map(() => `<td class="src">—</td>`).join("") + `</tr>`; continue; }
      const seg = dr.meta.segment ? ` <span class="src">${esc(dr.meta.segment)} · ${esc(tpl)}</span>` : ` <span class="src warn">사업부 없음 — 가정을 적용할 수 없다</span>`;
      h += `<tr><td>${esc(dr.id)} ${esc(dr.label)}${seg}</td>` + SCEN.map(n => {
        const das = (scs[n] && scs[n].driver_assumptions) || [];
        const da = das.find(x => x.node_id === dr.id && !x.variable) || das.find(x => x.node_id === dr.id);
        const u = da && da.unit ? da.unit : unit;
        const unitSel = tpl === "direct_op" ? `<select name="u:${n}:${dr.id}"><option value="%"${u === "%" ? " selected" : ""}>%</option><option value="억원"${u === "억원" ? " selected" : ""}>억원</option></select>` : `<span class="src">${unit}</span>`;
        return `<td><input name="d:${n}:${dr.id}" type="number" step="any" placeholder="0" value="${da && da.delta_pct != null ? da.delta_pct : ""}"> ${unitSel}</td>`;
      }).join("") + `</tr>`;
    }
    h += `<tr><td>적용 PER <span class="src">비우면 기본값</span></td>` + SCEN.map(n => `<td><input name="per:${n}" type="number" step="any" placeholder="${V && V.per != null ? V.per.toFixed(2) : ""}" value="${scs[n] && scs[n].per != null ? scs[n].per : ""}"></td>`).join("") + `</tr>`;
    h += `<tr><td>EPS 기준</td>` + SCEN.map(n => `<td><select name="eb:${n}"><option value="">(A3.8 기준)</option>` + ["consensus", "my_estimate", "normalized", "user"].map(b => `<option value="${b}"${scs[n] && scs[n].eps_basis === b ? " selected" : ""}>${b}</option>`).join("") + `</select></td>`).join("") + `</tr>`;
    h += `<tr><td>확률 (%) <span class="src">사람 몫 · 합 100</span></td>` + SCEN.map(n => `<td><input name="p:${n}" type="number" step="any" min="0" max="100" value="${scs[n] && scs[n].probability != null ? Math.round(scs[n].probability * 1000) / 10 : ""}"></td>`).join("") + `</tr>`;
    h += `</tbody></table>`;
    const mult = crit.sotp_multiples ? Object.entries(crit.sotp_multiples).map(([k, v]) => `${k}=${v}`).join(", ") : "";
    h += `<fieldset><legend>평가 기준 (A3.8) — 비우면 기본값(표시됨)</legend><div class="row">` +
      `<label>EPS 기준 <select name="c:eps_basis"><option value="">consensus(기본)</option>` + ["consensus", "my_estimate", "normalized", "user"].map(b => `<option value="${b}"${crit.eps_basis === b ? " selected" : ""}>${b}</option>`).join("") + `</select></label>` +
      `<label>EPS(입력) <input name="c:eps" type="number" step="any" value="${crit.eps != null ? crit.eps : ""}"></label>` +
      `<label>적용 PER <input name="c:per" type="number" step="any" placeholder="${V && V.per != null ? V.per.toFixed(2) : ""}" value="${crit.per != null ? crit.per : ""}"></label>` +
      `<label>주식 수 <input name="c:shares" type="number" step="any" placeholder="${V && V.shares_out != null ? V.shares_out : ""}" value="${crit.shares != null ? crit.shares : ""}"></label>` +
      `<label>순현금(억원) <input name="c:net_cash" type="number" step="any" placeholder="${V && V.net_cash != null ? Math.round(V.net_cash) : "표"}" value="${crit.net_cash != null ? crit.net_cash : ""}"></label>` +
      `<label>DPS <input name="c:dps" type="number" step="any" placeholder="${V && V.dps != null ? V.dps : "표"}" value="${crit.dps != null ? crit.dps : ""}"></label>` +
      `<label>배당 <select name="c:dividend_in_return"><option value="">separate(기본)</option>` + ["separate", "include", "exclude"].map(b => `<option value="${b}"${crit.dividend_in_return === b ? " selected" : ""}>${b}</option>`).join("") + `</select></label></div>` +
      `<label>SOTP 멀티플 (사용자 입력만) <input class="wide" name="c:sotp" type="text" placeholder="메모리=6, 파운드리=0, DX=8, SDC=6, Harman=10 — 사업부: ${esc(((V && V.segments) || []).join(" · "))}" value="${esc(mult)}"></label></fieldset>`;
    calcBody.innerHTML = h;
    calcRes.style.display = "none";
    document.getElementById("calc-note").textContent = EDIT.server ? "" : "편집 서버가 없어 미리보기는 안 된다 — 「적용」 뒤 저장(edits.json) 하고 `mindmap scenario <맵>` 으로 본다";
    document.getElementById("calc-preview").disabled = !EDIT.server;
  }
  function calcOps() {
    const q = name => calcBody.querySelector(`[name="${CSS.escape(name)}"]`);
    const ops = [];
    const crit = critData();
    const setOrDel = (k, v) => { if (v == null || v === "") delete crit[k]; else crit[k] = v; };
    setOrDel("eps_basis", q("c:eps_basis").value || null); setOrDel("eps", numOr(q("c:eps").value)); setOrDel("per", numOr(q("c:per").value));
    setOrDel("shares", numOr(q("c:shares").value)); setOrDel("net_cash", numOr(q("c:net_cash").value)); setOrDel("dps", numOr(q("c:dps").value));
    setOrDel("dividend_in_return", q("c:dividend_in_return").value || null);
    const mt = {}; for (const kv of q("c:sotp").value.split(/[,;\n]/)) { const [k, v] = kv.split("="); if (k && k.trim() && v != null && v.trim() !== "" && Number.isFinite(Number(v))) mt[k.trim()] = Number(v); }
    if (Object.keys(mt).length) crit.sotp_multiples = mt; else delete crit.sotp_multiples;
    const before = critData();
    if (JSON.stringify(crit) !== JSON.stringify(before)) ops.push({ op: "set", id: "A3.8", fields: { value: Object.keys(crit).length ? crit : null } });
    const norm = o => { if (Array.isArray(o)) return o.map(norm); if (o && typeof o === "object") { const r = {}; for (const [k, v] of Object.entries(o)) { if (k === "model" || v == null || v === "" || (Array.isArray(v) && !v.length)) continue; r[k] = norm(v); } return r; } return o; };
    for (const n of SCEN) {
      const sc = scenData(n) || { name: n, assumptions: [], probability: null, target_price: null, horizon_months: 12, valuation_method: "" };
      const orig = JSON.stringify(norm(scenData(n)));
      sc.price_source = "computed";
      sc.driver_assumptions = [];
      for (const dr of calcDrivers()) {
        const el = q(`d:${n}:${dr.id}`); if (!el) continue;   // 가격 경로 밖(주주환원·수주) 행은 입력이 없다
        const v = numOr(el.value); if (v == null) continue;
        const us = q(`u:${n}:${dr.id}`);
        sc.driver_assumptions.push({ node_id: dr.id, driver_id: dr.meta.driver_id || null, variable: null, delta_pct: v, value: null, unit: us ? us.value : null, note: "" });
      }
      sc.per = numOr(q(`per:${n}`).value); sc.eps_basis = q(`eb:${n}`).value || null;
      const pv = numOr(q(`p:${n}`).value); sc.probability = pv == null ? null : (pv > 1 ? pv / 100 : pv);
      delete sc.model;
      if (JSON.stringify(norm(sc)) !== orig) ops.push({ op: "set", id: SCEN_ID[n], fields: { scenario: sc } });
    }
    return ops;
  }
  function renderCalcResult(tree, errs, warns) {
    const L = [];
    for (const n of SCEN) {
      const d = findIn(tree, SCEN_ID[n]); const sc = d && d.edit.sub && d.edit.sub.kind === "scenario" ? d.edit.sub.data : null; const r = sc && sc.model;
      if (!r) { L.push(`${n}: 시나리오 없음`); continue; }
      if (r.status !== "계산됨") { L.push(`${n}: 미계산 — 필요: ${(r.missing || []).join("; ")}`); }
      else L.push(`${n}: OP ${(r.op / 1e4).toFixed(1)}조 · EPS ${fmtNum(Math.round(r.eps))} × PER ${r.per.toFixed(2)} → ${fmtNum(Math.round(r.price))}원 (${r.price_vs_p0_pct >= 0 ? "+" : ""}${r.price_vs_p0_pct.toFixed(0)}%)` + (sc.probability != null ? ` · p=${sc.probability}` : " · p=미입력"));
      for (const w of (r.warnings || [])) L.push(`   ⚠ ${w}`);
      const by = r.op_delta_by_node || {}; const ks = Object.keys(by); if (ks.length) L.push("   ΔOP: " + ks.map(k => `${k} ${(by[k] / 1e4 >= 0 ? "+" : "")}${(by[k] / 1e4).toFixed(1)}조`).join(" · "));
    }
    for (const id of ["A3.9", "A3.10", "A3.11"]) { const d = findIn(tree, id); if (d && d.edit.value) L.push(`${id} ${d.edit.label}: ${d.edit.value}`); }
    if (errs && errs.length) L.push("⚠ 저장하면 검증 오류: " + errs.join(" · "));
    if (warns && warns.length) L.push("⚑ " + warns.filter(w => /밸류|시나리오|Bear|Bull|미계산/.test(w)).join(" · "));
    calcRes.innerHTML = L.map(x => x.startsWith("   ⚠") || x.startsWith("⚠") ? `<span class="warn">${esc(x)}</span>` : esc(x)).join("\n");
    calcRes.style.display = "";
  }
  async function calcPreview() {
    const ops = calcOps();
    if (!EDIT.server) { showToast("편집 서버가 없다 — 「적용」 뒤 저장하고 CLI 로 본다"); return; }
    let r;
    try { r = await api("/preview", { edits: [...edits, ...ops] }); } catch (e) { showToast("편집 서버에 닿지 않는다 — " + e); return; }
    if (!r.ok) { calcRes.innerHTML = `<span class="warn">${esc(r.error || "적용 실패")}</span>`; calcRes.style.display = ""; return; }
    renderCalcResult(r.tree, r.new_errors, r.warnings);
  }
  async function calcApply() {
    const ops = calcOps();
    if (!ops.length) { showToast("바뀐 것이 없다"); return; }
    for (const op of ops) { const err = await commit(op); if (err) { showToast("거절 — " + err); return; } }
    showToast(`가정 묶음 ${ops.length}건을 편집 목록에 넣었다 (저장 전) — 「저장」 으로 맵에 반영`);
    buildCalc();
    if (EDIT.server) await calcPreview();
  }
  function openCalc() { if (!EDIT) return; buildCalc(); calc.classList.add("show"); editor.classList.remove("show"); panel.classList.remove("show"); }
  function closeCalc() { calc.classList.remove("show"); }
  if (EDIT) {
    document.getElementById("calcbtn").addEventListener("click", openCalc);
    document.getElementById("calc-close").addEventListener("click", closeCalc);
    document.getElementById("calc-preview").addEventListener("click", calcPreview);
    document.getElementById("calc-apply").addEventListener("click", calcApply);
    document.getElementById("calc-reset").addEventListener("click", buildCalc);
    window.addEventListener("keydown", e => { if (e.key === "Escape") closeCalc(); });
  } else {
    document.getElementById("calcbtn").style.display = "none";
  }

  // ---------- 상호작용
  let drag = null;
  stage.addEventListener("mousedown", e => { if (e.target.closest(".node")) return; drag = { x: e.clientX - tx, y: e.clientY - ty }; stage.classList.add("dragging"); });
  window.addEventListener("mousemove", e => { if (!drag) return; tx = e.clientX - drag.x; ty = e.clientY - drag.y; applyTransform(); });
  window.addEventListener("mouseup", () => { drag = null; stage.classList.remove("dragging"); });
  stage.addEventListener("wheel", e => {
    e.preventDefault();
    const f = e.deltaY < 0 ? 1.1 : 1 / 1.1, nk = Math.max(0.2, Math.min(3, k * f));
    const rect = stage.getBoundingClientRect(), mx = e.clientX - rect.left, my = e.clientY - rect.top;
    tx = mx - (mx - tx) * (nk / k); ty = my - (my - ty) * (nk / k); k = nk; applyTransform();
  }, { passive: false });
  window.addEventListener("resize", fit);
  window.addEventListener("keydown", e => { if (e.key === "f") fit(); });
  document.getElementById("fit").addEventListener("click", fit);
  document.getElementById("expand").addEventListener("click", () => { for (const n of nodes.values()) n.open = true; render(); fit(); });
  document.getElementById("collapse").addEventListener("click", () => { for (const n of nodes.values()) n.open = n.depth === 0; render(); fit(); });
  document.getElementById("layout").addEventListener("change", e => { layout = e.target.value; render(); fit(); });
  document.getElementById("layout").value = layout;
  try { const saved = localStorage.getItem(key); if (saved) root.setAttribute("data-theme", saved); } catch (e) {}
  document.getElementById("theme").addEventListener("click", () => {
    const dark = root.getAttribute("data-theme") === "dark" || (!root.getAttribute("data-theme") && window.matchMedia("(prefers-color-scheme: dark)").matches);
    const next = dark ? "light" : "dark"; root.setAttribute("data-theme", next);
    try { localStorage.setItem(key, next); } catch (e) {}
  });

  render(); fit();
  window.__mindmap = { nodes, render, fit, get layout() { return layout; },
    set layout(v) { layout = v; document.getElementById("layout").value = v; render(); fit(); } };
})();
"""


def edit_options(m: MindMap, *, server: bool = False, token: str | None = None, today: date | None = None) -> dict[str, Any]:
    """HTML 편집 UI 설정 (OPTS.edit). server=True 면 같은 주소의 로컬 편집 서버(/preview, /save) 를 쓰고, 아니면 「저장」이 edits.json 을 내려준다."""
    from ..generate.edits import edit_hash, edit_schemas

    out = {"server": server, "token": token or "", "map_id": m.map_id, "base_hash": edit_hash(m), "schemas": edit_schemas(),
           "today": (today or date.today()).isoformat()}
    # 설계 v6: 계산기 머리말 — 현재가·기준 연도·컨센 EPS·목표 PER 기본값·사업부·원인 드라이버의 사업부/템플릿 (값은 서버가 계산한다)
    draft = m.meta.get("draft") or {}
    if isinstance(m, NewMap):
        fin = draft.get("financials") or {}
        vm = draft.get("valuation") or {}
        att = draft.get("attractiveness") or {}
        drivers = {k: {kk: v.get(kk) for kk in ("segment", "template", "variable", "driver_id")} for k, v in (draft.get("drivers") or {}).items()
                   if isinstance(v, dict) and v.get("role") != "result"}
        out["valuation"] = {"p0": m.current_price, "year": fin.get("year"), "eps": (fin.get("eps") or {}).get("mean"), "per": vm.get("per"),
                            "shares_out": (fin.get("shares_out") or {}).get("median"), "net_cash": vm.get("net_cash"), "dps": att.get("dps"),
                            "line": m["A3.9"].value if "A3.9" in m and isinstance(m["A3.9"].value, str) else None,
                            "segments": sorted((draft.get("segments") or {}).keys()), "drivers": drivers}
    return out


def to_html(m: MindMap, *, layout: str = "balanced", summary: bool = False, fold: set[str] | None = None,
            max_depth: int | None = None, expand_level: int | None = 2, edit: dict[str, Any] | None | bool = True) -> str:
    """단일 HTML, 외부 의존성 없음. layout: balanced | radial | right. expand_level: 처음 펼쳐 보이는 깊이 (None=전부).

    edit: True(기본) 면 편집 UI 를 넣되 저장은 edits.json 내려받기; `edit_options(..., server=True)` 를 주면 로컬 서버에 바로 저장;
    False/None 이면 편집 UI 없음. 요약 뷰(summary) 는 항상 편집 없음."""
    if layout not in ("balanced", "radial", "right"):
        raise ValueError(f"layout must be balanced|radial|right, got {layout!r}")
    tree = build_tree(m, summary=summary, fold=fold, max_depth=max_depth)
    title = html.escape(root_title(m).replace("*", ""))
    if summary or not edit:
        edit_opts = None
    elif edit is True:
        edit_opts = edit_options(m)
    else:
        edit_opts = edit
    opts = {"layout": layout, "expandLevel": expand_level, "edit": edit_opts}
    data_js = json.dumps(tree, ensure_ascii=False).replace("</", "<\\/")
    opts_js = json.dumps(opts, ensure_ascii=False)
    js = _JS.replace("__DATA__", data_js).replace("__OPTS__", opts_js)
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{_CSS}</style>
</head>
<body>
<div id="bar">
  <select id="layout" title="레이아웃">
    <option value="balanced">양쪽 균형</option>
    <option value="radial">방사형</option>
    <option value="right">오른쪽</option>
  </select>
  <button id="expand" type="button" title="모두 펼치기">펼치기</button>
  <button id="collapse" type="button" title="1단계만 남기고 접기">접기</button>
  <button id="fit" type="button" title="화면 맞춤 (f)">맞춤</button>
  <button id="copyall" type="button" title="맵 전체를 Markdown 목록으로 클립보드에">전체 복사</button>
  <button id="calcbtn" type="button" data-edit title="밸류에이션 & 시나리오 계산기 — 드라이버 가정 묶음 → 통합 모델 → Bear/Base/Bull 가격 (설계 v6)">밸류 계산기</button>
  <button id="theme" type="button" title="밝게/어둡게">☀ / ☾</button>
</div>
<div id="calc">
  <h3>밸류에이션 & 시나리오 계산기</h3>
  <button id="calc-close" class="close" type="button" title="닫기 (Esc)">닫기</button>
  <div id="calc-body"></div>
  <div class="row">
    <button id="calc-preview" type="button" class="primary" title="서버의 통합 모델로 계산해 보여 준다 (저장 안 함)">미리보기</button>
    <button id="calc-apply" type="button" title="이 가정 묶음·평가 기준을 편집 목록에 넣는다 — 위쪽 「저장」 으로 맵에 반영">적용</button>
    <button id="calc-reset" type="button">다시 읽기</button>
    <span class="note" id="calc-note"></span>
  </div>
  <div id="calc-res" class="res" style="display:none"></div>
  <div class="note">가정은 컨센(또는 선택한 EPS 기준) 경로 대비 변화다. 같은 사업부의 가격·물량이 함께 움직이면 교차항이 들어가고, 민감도(1변수) 를 더한 값과 다르다. 확률·멀티플·가정은 사람 몫 — 비워 두면 미계산으로 남는다. SOTP 는 검증용(멀티플 입력 뒤).</div>
</div>
<div id="menu">
  <div data-act="edit" data-edit>✎ 편집… (더블클릭 · F2)</div>
  <div data-act="addchild" data-edit>＋ 자식 노드 추가… (Insert)</div>
  <div data-act="delete" data-edit>✕ 이 노드 삭제 (Delete)</div>
  <div data-act="calc" data-edit>∑ 밸류 계산기…</div>
  <div class="sep" data-edit></div>
  <div data-act="line">한 줄 복사</div>
  <div data-act="detail">상세 복사 (값·질문·출처)</div>
  <div data-act="tree">이 가지 전체 복사 (Markdown)</div>
  <div data-act="id">노드 id 복사</div>
  <div class="sep"></div>
  <div data-act="panel">상세 보기 (복사용 글상자)</div>
</div>
<div id="editbar">
  <span class="count">고침 0건</span>
  <button id="ed-undo" type="button" title="마지막 편집 되돌리기 (Ctrl+Z)">되돌리기</button>
  <button id="ed-save" type="button" class="primary" title="맵에 반영 (Ctrl+S)">저장</button>
  <button id="ed-discard" type="button" title="저장하지 않은 편집을 모두 버린다">모두 취소</button>
  <span class="errs"></span>
</div>
<div id="editor">
  <h3 id="editor-title"></h3>
  <button id="editor-close" class="close" type="button" title="닫기 (Esc)">닫기</button>
  <form id="eform" autocomplete="off"></form>
  <div class="row">
    <button id="ed-apply" type="button" class="primary" title="이 노드의 편집을 목록에 넣는다 (Ctrl+S)">적용</button>
    <button id="ed-cancel" type="button">취소</button>
    <span style="flex:1"></span>
    <button id="ed-delete" type="button" class="danger" title="이 노드와 아래 가지를 지운다 (두 번 누름)">삭제</button>
  </div>
  <div class="note">적용은 화면에만 반영된다. 위쪽 「저장」 을 눌러야 맵 파일에 들어간다. 고친 노드는 ✎ 표시와 함께 user 태그를 얻고 초안 표시(draft·pending·⚑) 가 떨어진다.</div>
</div>
<div id="panel">
  <h3 id="panel-title"></h3>
  <button id="panel-close" class="close" type="button" title="닫기 (Esc)">닫기</button>
  <textarea id="panel-text" spellcheck="false"></textarea>
  <div class="row">
    <button id="panel-copy" type="button">상세 복사</button>
    <button id="panel-line" type="button">한 줄 복사</button>
    <button id="panel-tree" type="button">가지 복사 (Markdown)</button>
  </div>
</div>
<div id="toast"></div>
<div id="stage">
  <div id="viewport">
    <svg id="edges" xmlns="http://www.w3.org/2000/svg"></svg>
    <div id="nodes"></div>
  </div>
</div>
<div id="hint">클릭 = 접기/펼치기 · 더블클릭/✎ = 편집 · 우클릭 = 편집·복사 메뉴 · 글자 긁기·⧉·Ctrl+C = 복사 (Shift: 상세) · F2 편집 · Insert 자식 추가 · Delete 삭제 · Ctrl+Z 되돌리기 · Ctrl+S 저장 · 드래그 = 이동 · 휠 = 확대/축소 · f = 맞춤</div>
<script>{js}</script>
</body>
</html>
"""


def write_html(m: MindMap, path: str | Path, **kw: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(to_html(m, **kw), encoding="utf-8")
    return p


__all__ = ["to_html", "write_html", "build_tree", "edit_options"]
