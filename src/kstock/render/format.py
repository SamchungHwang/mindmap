"""노드 한 줄 표기 — 렌더러 공통."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..models import MindMap, Node, NodeStatus, NodeType

LIGHT_ICON = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}
STATUS_BADGE = {
    NodeStatus.confirmed: "",
    NodeStatus.estimated: "≈",
    NodeStatus.unverified: "?",
    NodeStatus.stale: "⏳",
}


def fmt_num(v: Any, unit: str | None = None, value_type: str | None = None) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "예" if v else "아니오"
    if isinstance(v, (int, float)):
        if value_type == "percent" or unit == "%":
            return f"{v * 100:.1f}%" if abs(v) <= 1.5 else f"{v:.1f}%"
        if value_type == "price" or unit == "원":
            return f"{v:,.0f}원"
        if value_type == "probability":
            return f"p={v:.2f}"
        if abs(v) >= 1000:
            return f"{v:,.0f}" + (unit or "")
        return (f"{v:.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)) + (f" {unit}" if unit else "")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, dict):
        if "text" in v and ("impact_pct" in v or "probability" in v):
            # 하락 리스크 {probability, impact_pct, text} 는 글이 먼저
            p, imp = v.get("probability"), v.get("impact_pct")
            tail = [f"확률 {p:.0%}" if isinstance(p, (int, float)) else "", f"영향 {imp:+.0f}%" if isinstance(imp, (int, float)) else ""]
            return f"{v['text']} ({', '.join(t for t in tail if t)})"[:220]
        pct_keys = {"suggested", "confirmed", "weight", "w_now", "w_target", "cash_weight", "probability"}
        items = []
        for k, val in v.items():
            if str(k).startswith("_"):
                continue
            if isinstance(val, dict):
                inner = ", ".join(f"{ik} {fmt_num(iv)}" for ik, iv in val.items() if not isinstance(iv, (dict, list)))
                items.append(f"{k}({inner})")
            elif isinstance(val, list):
                items.append(f"{k}={'~'.join(fmt_num(x) for x in val)}")
            elif k in pct_keys and isinstance(val, (int, float)) and abs(val) <= 1.5:
                items.append(f"{k}={val * 100:.1f}%")
            else:
                items.append(f"{k}={fmt_num(val)}")
        return ", ".join(items)[:160]
    if isinstance(v, list):
        return ", ".join(str(x) for x in v[:6]) + (" …" if len(v) > 6 else "")
    return str(v)


def node_line(n: Node, *, show_question: bool = True, show_meta: bool = True) -> str:
    """노드 1개를 마크다운 한 줄로. 값이 없으면 질문을 보여 준다 (미확인 상태를 드러내기 위해)."""
    badge = STATUS_BADGE.get(n.status, "")
    label = n.label
    if n.id.endswith("0") and "." not in n.id:
        label = f"**{label}**"
    # 자동 초안(draft) 은 ✎, 사람이 봐야 할 곳(warn) 은 ⚑ — 사람이 저장하면 떨어진다
    if "warn" in n.tags:
        label = "⚑ " + label
    if "draft" in n.tags:
        label = "✎ " + label
    parts: list[str] = []

    if n.driver is not None:
        d = n.driver
        u = n.unit or ""
        seq = []
        if d.at_entry is not None:
            seq.append(f"매수시 {fmt_num(d.at_entry)}{u}")
        if d.current.value is not None:
            seq.append(f"현재 {fmt_num(d.current.value)}{u}")
        if d.consensus.value is not None:
            seq.append(f"컨센 {fmt_num(d.consensus.value)}{u}")
        if d.my_estimate.value is not None:
            seq.append(f"내 추정 {fmt_num(d.my_estimate.value)}{u}")
        s = " → ".join(seq)
        if d.direction:
            s += f" ⟨{d.direction.value}⟩"
        if d.next_check:
            s += f" · 확인 {d.next_check.isoformat()}"
        if d.kind == "qualitative" and not seq:
            s = "정성 — " + (d.check_indicator[:60] if d.check_indicator else "관측 지표 미정")
        if d.sensitivity is not None and d.role == "cause":
            from ..engine.sensitivity import sensitivity_short

            s += " · " + sensitivity_short(d.sensitivity)
        parts.append(s)
    elif n.scenario is not None:
        sc = n.scenario
        p = f"p={sc.probability:.2f}" if sc.probability is not None else "p=미입력"
        s = f"{fmt_num(sc.target_price, value_type='price')} ({p}, {sc.horizon_months}M)"
        if sc.assumptions:
            s += " — " + "; ".join(sc.assumptions[:3])
        parts.append(s)
    elif n.rule is not None:
        r = n.rule
        st = {"armed": "", "fired": " 🔥발동", "disabled": " (해제)"}[r.status]
        parts.append(f"⚡ `{r.condition}` → {r.action.value}" + (f" {r.size}" if r.size else "") + st)
    elif n.event is not None:
        e = n.event
        label = f"📅 {e.date.isoformat()}"
        parts.append(f"{e.fact} ⟨{e.direction.value}, 반영:{e.priced_in.value}, {e.materiality.value}⟩")
    elif n.value is not None:
        v = n.value
        if n.id in ("B0",) and isinstance(v, str) and v in LIGHT_ICON:
            parts.append(f"{LIGHT_ICON[v]} {v}")
        else:
            parts.append(fmt_num(v, n.unit, n.value_type.value))
    elif show_question and n.question and n.node_type != NodeType.question:
        parts.append(f"_{n.question}_")

    line = f"{label}" + (f": {' '.join(parts)}" if parts else "")
    if show_meta and n.node_type in (NodeType.fact, NodeType.estimate) and (badge or n.as_of):
        meta = " ".join(x for x in (badge, n.as_of.isoformat() if n.as_of else "") if x)
        line += f" <sub>{meta}</sub>"
    if n.links and n.node_type == NodeType.link:
        line += " → " + ", ".join(f"`{lk}`" for lk in n.links)
    return line


def root_title(m: MindMap) -> str:
    root = m.root()
    title = root.label
    if m.kind == "HOLD":
        light = m.value("B0")
        if light in LIGHT_ICON:
            title = f"{LIGHT_ICON[light]} {title}"
    return title


__all__ = ["fmt_num", "node_line", "root_title", "LIGHT_ICON"]
