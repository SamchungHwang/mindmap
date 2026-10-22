"""mermaid mindmap 렌더러 — 문서(GitHub/Notion/Obsidian) 삽입용. 값은 짧게 줄인다."""

from __future__ import annotations

import re

from ..models import MindMap, Node
from .format import fmt_num, root_title


def _clean(text: str, limit: int = 60) -> str:
    # mermaid mindmap 은 괄호·따옴표에 민감하다 → 치환 후 길이 제한
    t = text.replace("<", "＜").replace(">", "＞")
    t = re.sub(r"[()\[\]{}\"`*_|]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return (t[: limit - 1] + "…") if len(t) > limit else t


def _short(n: Node) -> str:
    if n.driver is not None:
        d = n.driver
        bits = [x for x in (
            f"매수시 {fmt_num(d.at_entry)}" if d.at_entry is not None else "",
            f"현재 {fmt_num(d.current.value)}" if d.current.value is not None else "",
            f"추정 {fmt_num(d.my_estimate.value)}" if d.my_estimate.value is not None else "",
        ) if x]
        return f"{n.label}: " + " → ".join(bits) if bits else n.label
    if n.scenario is not None:
        p = f"p={n.scenario.probability:.2f}" if n.scenario.probability is not None else "p=?"
        return f"{n.label} {fmt_num(n.scenario.target_price, value_type='price')} {p}"
    if n.rule is not None:
        return f"{n.label}: {n.rule.condition} → {n.rule.action.value}"
    if n.event is not None:
        return f"{n.event.date.isoformat()} {n.event.fact}"
    if n.value is not None:
        return f"{n.label}: {fmt_num(n.value, n.unit, n.value_type.value)}"
    return n.label


def to_mermaid(m: MindMap, *, max_depth: int = 3, skip: set[str] | None = None) -> str:
    skip = skip or {"A8", "B2.4", "C1.1", "C6.1"}
    root = m.root()
    lines = ["mindmap", f"  root(({_clean(root_title(m).replace('*', ''), 40)}))"]

    def walk(parent: Node, depth: int) -> None:
        if depth > max_depth:
            return
        for c in m.children(parent.id):
            if c.id in skip:
                lines.append("    " * depth + f"  {_clean(c.label)} …")
                continue
            lines.append("    " * depth + f"  {_clean(_short(c))}")
            walk(c, depth + 1)

    walk(root, 1)
    return "\n".join(lines) + "\n"


__all__ = ["to_mermaid"]
