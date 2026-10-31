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
#: 낡은 자료(status=stale) 의 값 앞 표시 — 첨자 ⏳ 와 별개로 본문에 바로 보인다
STALE_MARK = "⚠ 낡음 "


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
        s = (f"{fmt_num(sc.target_price, value_type='price')}" if sc.target_price is not None else "미계산") + f" ({p}, {sc.horizon_months}M)"
        if sc.price_source == "computed":
            # 설계 v6: 가정 묶음 → 통합 모델. 가정과 결과를 한 줄에
            das = [f"{d.node_id} {d.delta_pct:+g}{d.unit or '%'}" + (f" {d.variable}" if d.variable else "") for d in sc.driver_assumptions if d.delta_pct is not None]
            r = sc.model
            tail = ""
            if r is not None and r.status == "계산됨":
                tail = f" → OP {r.op / 1e4:,.0f}조 · EPS {r.eps:,.0f} × PER {r.per:.2f}" if r.op is not None and r.eps is not None and r.per else ""
            elif r is not None:
                tail = " — 미계산: " + "; ".join(r.missing)[:80]
            s += " — 가정 " + (" · ".join(das) if das else "없음(컨센 경로)") + (f" · PER {sc.per:g}" if sc.per else "") + tail
            if r is not None and r.warnings:
                s += " ⚠ " + r.warnings[0][:60]
        elif sc.assumptions:
            s += " — " + "; ".join(sc.assumptions[:3])
        parts.append(s)
    elif n.rule is not None:
        r = n.rule
        st = {"armed": "", "fired": " 🔥발동", "disabled": " (해제)"}[r.status]
        parts.append(f"⚡ `{r.condition}` → {r.action.value}" + (f" {r.size}" if r.size else "") + st)
    elif n.event is not None:
        e = n.event
        label = event_label(e)
        parts.append(event_line(e))
    elif n.value is not None:
        v = n.value
        if n.id in ("B0",) and isinstance(v, str) and v in LIGHT_ICON:
            parts.append(f"{LIGHT_ICON[v]} {v}")
        else:
            parts.append(fmt_num(v, n.unit, n.value_type.value))
    elif show_question and n.question and n.node_type != NodeType.question:
        parts.append(f"_{n.question}_")

    # 낡은 자료는 값 앞에 바로 적는다 — ⏳ 첨자만으로는 「추세 상승」 이 5주 전 시세라는 게 안 읽힌다 (2026-10-31 3차 검토)
    if n.status == NodeStatus.stale and parts and n.event is None and (n.value is not None or n.driver is not None or n.scenario is not None):
        parts[0] = STALE_MARK + (f"({n.as_of.isoformat()}) " if n.as_of else "") + parts[0]
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



# ---------------------------------------------------------------------------- 이벤트 (2026-10-30 A6 보강)
#: 화면에 보이는 확실성 말 — 「공시확정」 은 공시가 확정됐다는 건지 일정이 확정됐다는 건지 모호해서 (2026-10-31 검토)
CERTAINTY_LABEL = {"공시확정": "공시 확인·일정 확정", "공시상 예정": "공시 확인·예정", "과거 주기 추정": "과거 주기 추정", "회사 목표": "회사 목표 (원문 확인)",
                   "회사 계획 (리포트 인용)": "회사 계획 · 리포트 인용 (원문 미확인)",
                   "리포트 전망": "리포트 전망", "통상 일정 추정": "통상 일정 추정"}
STATUS_MARK = {"completed": "✓ ", "delayed": "⏳ ", "cancelled": "✗ ", "scheduled": "예정 "}


def event_label(e) -> str:  # noqa: ANN001
    """한 줄의 머리 — 📅 시점 [확실성]. 기간 사건은 기간을 보이고, 지연·완료·취소는 앞에 표시한다."""
    tag = f" [{CERTAINTY_LABEL.get(e.certainty.value, e.certainty.value)}]" if e.certainty.value != "미정" else ""
    mark = STATUS_MARK.get(getattr(e, "status", None) and e.status.value, "")
    return f"📅 {mark}{e.when_label()}{tag}"


def event_line(e) -> str:  # noqa: ANN001
    """한 줄의 몸 — 사실 ⟨중요도 · 반영⟩ 과 사건 뒤 결과. 방향은 사건 뒤(outcome.verdict) 에만 뜻이 있어 「미정」 이면 안 적는다."""
    bits = [e.materiality.value, f"반영:{e.priced_in.value}"]
    if e.direction.value != "미정":
        bits.insert(0, e.direction.value)
    if getattr(e, "stage", None):
        bits.append(f"단계:{e.stage}")
    if getattr(e, "delivery_period", None):
        bits.append(f"납기 {e.delivery_period}")
    s = f"{e.fact} ⟨{', '.join(bits)}⟩"
    o = e.outcome
    if o is not None and (o.reaction_d1 is not None or o.verdict is not None or o.actual):
        res = []
        if o.verdict is not None:
            res.append(f"판정 {o.verdict.value}")
        if o.reaction_d1 is not None:
            res.append(f"D+1 {o.reaction_d1:+.1%}")
        if o.reaction_d5 is not None:
            res.append(f"D+5 {o.reaction_d5:+.1%}" + (f" (시장 대비 {o.vs_market_d5:+.1%})" if o.vs_market_d5 is not None else ""))
        if o.actual:
            res.append(o.actual[:40])
        s += " → " + " · ".join(res)
    return s


def event_detail_lines(e) -> list[str]:  # noqa: ANN001
    """상세 패널용 여러 줄 — 보강본이 자유 문장으로 적던 것을 칸에서 꺼내 같은 순서로 보인다."""
    st = getattr(e, "status", None)
    lines = [f"시점: {e.when_label()} / 구분: {CERTAINTY_LABEL.get(e.certainty.value, e.certainty.value)} / 중요도: {e.materiality.value} / 선반영: {e.priced_in.value}"
             + (f" / 상태: {st.value}" if st is not None and st.value != "scheduled" else "")]
    if getattr(e, "event_id", None):
        lines.append(f"ID: {e.event_id}")
    if e.direction.value != "미정":
        lines.append(f"방향: {e.direction.value}")
    if e.precheck:
        lines.append("사전 확인: " + " · ".join(e.precheck))
    if e.bull:
        lines.append("상방 증거: " + " · ".join(e.bull))
    if e.bear:
        lines.append("하방 증거: " + " · ".join(e.bear))
    if e.rule:
        lines.append(f"판단 규칙: {e.rule}")
    if getattr(e, "evidence", None):
        lines.append("관련 전망·근거 (판정 조건 아님): " + " / ".join(e.evidence[:4]))
    if getattr(e, "program", None) or getattr(e, "project", None):
        lines.append("사업: " + " · ".join(x for x in (getattr(e, "program", None), f"묶음 {e.project}" if getattr(e, "project", None) else None) if x))
    if getattr(e, "stage", None):
        lines.append(f"진행 단계: {e.stage}")
    if getattr(e, "delivery_period", None):
        lines.append(f"납기·운전 시점: {e.delivery_period} (사건 시점이 아니다)")
    if e.affects:
        lines.append("영향 노드: " + ", ".join(e.affects))
    if e.next_check:
        lines.append(f"체크포인트: {e.next_check.isoformat()}")
    o = e.outcome
    if o is not None:
        res = []
        if o.actual:
            res.append(f"실제: {o.actual}")
        if o.reaction_d1 is not None:
            res.append(f"D+1 {o.reaction_d1:+.1%}")
        if o.reaction_d5 is not None:
            res.append(f"D+5 {o.reaction_d5:+.1%}")
        if o.vs_market_d5 is not None:
            res.append(f"시장 대비 {o.vs_market_d5:+.1%}")
        if o.verdict is not None:
            res.append(f"판정 {o.verdict.value}")
        if o.as_of:
            res.append(f"({o.as_of.isoformat()})")
        lines.append("사건 뒤: " + " · ".join(res) if res else "사건 뒤: (아직)")
        if o.note:
            lines.append(f"  {o.note}")
    if e.source is not None and (e.source.name or e.source.url):
        lines.append("출처: " + " ".join(x for x in (e.source.name, e.source.url) if x))
    return lines


__all__ = ["fmt_num", "node_line", "root_title", "LIGHT_ICON", "STALE_MARK", "event_label", "event_line", "event_detail_lines"]
