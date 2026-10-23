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
from pathlib import Path
from typing import Any

from ..models import HoldingMap, MindMap, Node
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


def _tree(m: MindMap, parent: Node, fold: set[str], max_depth: int | None, depth: int) -> dict[str, Any]:
    line = node_line(parent)
    node: dict[str, Any] = {"id": parent.id, "html": _md_inline(line), "kind": _kind(parent), "text": _plain(line),
                            "detail": _detail(parent), "fold": parent.id in fold, "children": []}
    if max_depth is None or depth < max_depth:
        node["children"] = [_tree(m, c, fold, max_depth, depth + 1) for c in m.children(parent.id)]
    return node


def build_tree(m: MindMap, *, summary: bool = False, fold: set[str] | None = None,
               max_depth: int | None = None) -> dict[str, Any]:
    """맵 → 중첩 dict. 중심 노드의 보조 필드(A0.x / B0.x)는 중심의 첫 자식 묶음으로 둔다."""
    fold = FOLD_BY_DEFAULT if fold is None else fold
    root = m.root()
    title = root_title(m).replace("*", "")
    tree: dict[str, Any] = {"id": root.id, "html": f"<b>{html.escape(title)}</b>", "kind": "root", "fold": False, "children": [],
                            "text": title, "detail": _detail(root)}
    if summary and isinstance(m, HoldingMap):
        for nid in ("B0", "B0.1", "B2.3", "B6", "B3.4", "B4.1"):
            if nid in m:
                tree["children"].append({"id": f"s:{nid}", "html": _md_inline(node_line(m[nid])), "kind": _kind(m[nid]),
                                         "text": _plain(node_line(m[nid])), "detail": _detail(m[nid]), "fold": False, "children": []})
        nxt = sorted({d.driver.next_check for d in m.drivers() if d.driver and d.driver.next_check})
        if nxt:
            tree["children"].append({"id": "next", "html": f"다음 확인일: {nxt[0].isoformat()}", "kind": "computed", "fold": False, "children": [],
                                     "text": f"다음 확인일: {nxt[0].isoformat()}", "detail": f"다음 확인일: {nxt[0].isoformat()}"})
        return tree
    first = m.children(root.id)
    aux = [c for c in first if "." in c.id]
    branches = [c for c in first if "." not in c.id]
    if aux:
        tree["children"].append({"id": f"{root.id}.*", "html": "기본 정보", "kind": "question", "fold": False, "text": "기본 정보", "detail": "기본 정보",
                                 "children": [_tree(m, c, fold, max_depth, 2) for c in aux]})
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
.node .copy { position:absolute; top:-9px; right:-9px; display:none; width:20px; height:20px; line-height:18px; text-align:center;
  border-radius:50%; background:var(--btn); color:var(--fg); border:1px solid var(--box-border); font-size:12px; cursor:pointer; user-select:none; }
.node:hover .copy { display:block; }
.node .copy:hover { background:var(--root-bg); color:#fff; }
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
  const nodes = new Map();         // id -> {d, el, open, w, h, x, y, color, depth}

  // ---------- 트리 준비
  (function walk(d, depth, color) {
    const open = !d.fold && (OPTS.expandLevel == null || depth < OPTS.expandLevel || depth === 0);
    nodes.set(d.id, { d, open, depth, color, children: d.children.map(c => c.id) });
    d.children.forEach((c, i) => walk(c, depth + 1, depth === 0 ? COLORS[i % COLORS.length] : color));
  })(DATA, 0, null);

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
        select(id);
        // 글자를 긁어 선택한 뒤의 클릭은 접기/펼치기가 아니다 — 복사하려는 것
        const sel = window.getSelection && window.getSelection();
        if (sel && sel.toString().trim() && el.contains(sel.anchorNode)) return;
        if (n.children.length) { n.open = !n.open; render(); }
      });
      el.addEventListener("dblclick", (e) => { e.stopPropagation(); e.preventDefault(); openPanel(id); });
      el.addEventListener("contextmenu", (e) => { e.preventDefault(); e.stopPropagation(); select(id); openMenu(id, e.clientX, e.clientY); });
      layer.appendChild(el);
      n.el = el;
    }
    const more = (!n.open && n.children.length) ? `<span class="more">+${descendants(id)}</span>` : "";
    n.el.innerHTML = n.d.html + more + `<span class="copy" title="한 줄 복사 (우클릭: 더 보기)">⧉</span>`;
    n.el.classList.toggle("selected", selectedId === id);
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
    const vis = visible(DATA.id, []);
    vis.forEach(n => ensure(n.d.id));
    const r = nodes.get(DATA.id);
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

  // ---------- 복사 · 상세 (노드 글자는 그대로 긁어 복사할 수 있고, ⧉ / 우클릭 / 더블클릭 / Ctrl+C 도 된다)
  let selectedId = null;
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
    menu.dataset.id = id; menu.style.display = "block";
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
  document.getElementById("copyall").addEventListener("click", () => copyText(subtreeMarkdown(DATA.id, 0), "전체 복사 (Markdown)"));
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


def to_html(m: MindMap, *, layout: str = "balanced", summary: bool = False, fold: set[str] | None = None,
            max_depth: int | None = None, expand_level: int | None = 2) -> str:
    """단일 HTML, 외부 의존성 없음. layout: balanced | radial | right. expand_level: 처음 펼쳐 보이는 깊이 (None=전부)."""
    if layout not in ("balanced", "radial", "right"):
        raise ValueError(f"layout must be balanced|radial|right, got {layout!r}")
    tree = build_tree(m, summary=summary, fold=fold, max_depth=max_depth)
    title = html.escape(root_title(m).replace("*", ""))
    opts = {"layout": layout, "expandLevel": expand_level}
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
  <button id="theme" type="button" title="밝게/어둡게">☀ / ☾</button>
</div>
<div id="menu">
  <div data-act="line">한 줄 복사</div>
  <div data-act="detail">상세 복사 (값·질문·출처)</div>
  <div data-act="tree">이 가지 전체 복사 (Markdown)</div>
  <div data-act="id">노드 id 복사</div>
  <div class="sep"></div>
  <div data-act="panel">상세 보기 (편집·선택 가능)</div>
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
<div id="hint">노드 클릭 = 접기/펼치기 · 글자 긁기 = 선택·복사 · ⧉/우클릭 = 복사 메뉴 · 더블클릭 = 상세 · Ctrl+C = 고른 노드 복사 (Shift: 상세) · 드래그 = 이동 · 휠 = 확대/축소 · f = 맞춤</div>
<script>{js}</script>
</body>
</html>
"""


def write_html(m: MindMap, path: str | Path, **kw: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(to_html(m, **kw), encoding="utf-8")
    return p


__all__ = ["to_html", "write_html", "build_tree"]
