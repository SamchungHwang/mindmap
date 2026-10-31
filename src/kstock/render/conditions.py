"""HTML 「성립 조건」 패널 (설계 v7 T9) — 표 다섯(가정 표 · 역산 표 · 필요 경로 · 증거 계획 · 판단 수정 규칙) + 신호등 + 판단 상태.
서버가 HTML 문자열을 만들어 OPTS.edit.conditions 로 넘기고 페이지는 보여 주기만 한다 (클라이언트에 식 복제 없음, 내려받기 모드에서도 보인다)."""

from __future__ import annotations

from html import escape

from ..engine.evidence import LIGHT_ICON, ROLE_LABEL, light_counts_text
from ..engine.revision import GRADE_LABEL
from ..models import Light, NewMap


def _tbl(head: list[str], rows: list[list[str]], cls: str = "") -> str:
    h = "".join(f"<th>{escape(x)}</th>" for x in head)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>'


def conditions_html(m: NewMap) -> str:
    draft = m.meta.get("draft") or {}
    cond = draft.get("conditions") or {}
    parts: list[str] = []
    fl = m.meta.get("flags") or {}
    gate = draft.get("claim_gate") or {}
    status = [f"판단 {escape(m.decision.value if m.decision else '작성중')}"]
    if fl.get("hold_new_buy"):
        status.append('<span class="warn">신규 BUY 보류</span>')
    if fl.get("review"):
        status.append('<span class="warn">재검토 중</span>')
    if fl.get("no_evidence"):
        status.append('<span class="warn">증거 없는 판단 (⚪ 과반)</span>')
    if fl.get("pending_proposals"):
        status.append(f"제안 대기 {fl['pending_proposals']}")
    if gate.get("status") == "needs_claim_review":
        status.append('<span class="warn">Claim 품질 게이트 미달</span>')
    parts.append(f'<div class="note">상태: {" · ".join(status)} · 신호등 {escape(light_counts_text(draft.get("lights") or {}))}</div>')
    # 1 가정 표
    at = cond.get("assumptions") or {}
    if at.get("rows"):
        brokers = at["brokers"]
        head = ["드라이버", *[f"{b} ({at['broker_tp'].get(b, '')})" for b in brokers], "컨센", "내 가정 (Base)"]
        rows = [[escape(r["label"]), *[escape(r["by_broker"].get(b) or "—") for b in brokers], escape(r.get("consensus") or "—"), escape(r.get("mine") or "—")] for r in at["rows"]]
        parts.append(f"<h4>① 가정 표 — 증권사가 적은 전망 · 컨센 · 내 가정 <span class='src'>{escape(at.get('note', ''))}</span></h4>" + _tbl(head, rows))
    # 2 역산 표
    inv = cond.get("inversion") or {}
    if inv.get("price"):
        per_txt = f"{inv['per']:.2f}, {escape(str(inv['per_source']))}" if inv.get("per") is not None else "없음 — 목표가·EPS 또는 A3.2 per 입력 전"
        parts.append(f"<h4>② 역산 표 — 현재가 {inv['price']:,.0f} 을 설명하는 조합 (적용 PER {per_txt}) <span class='src'>모든 줄은 나머지 고정의 수학적 해 — 시장의 기대가 아니다</span></h4>")
        rows = []
        for r in inv.get("single") or []:
            if r["status"] == "ok":
                v = f"{r['value']:+.1f}{r['unit']}" if r["unit"] != "배" else f"{r['value']:.2f}배"
                tag = "가능"
            else:
                v = f"{r['value']:+.0f}{r['unit']}" if r.get("value") is not None else "—"
                tag = {"out_of_range": "범위 밖", "no_solution": "해 없음", "not_applicable": "역산 없음"}.get(r["status"], r["status"])
            rows.append([escape(r["label"]), v, tag, f"<span class='src'>{escape(r.get('note') or '')}</span>"])
        parts.append(_tbl(["한 변수 해 (나머지 고정)", "값", "", "비고"], rows))
        pr = inv.get("pair")
        if pr:
            rows = [[f"{r['x']:+.0f}%", (f"{r['y']:.2f}" if pr["y"] == "per" else f"{r['y']:+.1f}%") if r.get("y") is not None else "—", f"{r['price']:,.0f}"] for r in pr["rows"]]
            parts.append(f"<div class='note'>두 변수 조합 — {escape(pr['x_label'])} × {escape(pr['y_label'])}</div>" + _tbl([pr["x_label"] + " (컨센 대비)", pr["y_label"], "계산 주가"], rows))
    # 3 필요 경로
    rows = []
    for n in m.drivers():
        d = n.driver
        if d is None or d.role == "result":
            continue
        if d.required is None:
            rows.append([escape(n.label.split(" (")[0]), "<span class='src'>경로 없음 — 분기 추정치 또는 사람 입력</span>", "", ""])
            continue
        pts = " · ".join(f"{escape(p.at)} {p.value}{'*' if p.source == 'interpolated' else ''}" for p in d.required.points[:8])
        rows.append([escape(n.label.split(" (")[0]), f"<span class='src'>{escape(d.required.ruler.label())}</span>", pts, f"<span class='src'>{escape(d.required.method)}</span>"])
    if rows:
        parts.append("<h4>③ 필요 경로 — 날짜별 (출처 표시 · * = 보간)</h4>" + _tbl(["드라이버", "자", "점", "출처"], rows))
    # 4 증거 계획
    ev = draft.get("evidence_plan") or []
    if ev:
        rows = []
        for r in ev:
            lt = Light(r["light"]) if r.get("light") else None
            rows.append([f"{LIGHT_ICON.get(lt)} {escape(r['label'])}", escape(ROLE_LABEL.get(r.get("role"), "—")), escape(r.get("observation") or "—"), escape(r.get("required") or "—"),
                         escape(r.get("band") or "—"), escape(r.get("bounds") or "—"), escape(r.get("next_check") or "—"), f"<span class='src'>{escape(r.get('reason') or '')}</span>"])
        parts.append("<h4>④ 증거 계획 — 관측값 vs 필요 경로 (확인된 관측값 · 같은 자만)</h4>" + _tbl(["드라이버", "역할", "관측", "필요값", "띠", "경계", "다음", "사유"], rows))
    # 5 규칙 · 이벤트
    if "A5.6" in m:
        rows = []
        for n in m.children("A5.6"):
            r = n.rule
            if r is None:
                continue
            rows.append([escape(n.id), escape(GRADE_LABEL.get(r.grade, r.grade or "—")), escape(m[r.driver_id].label.split(" (")[0] if r.driver_id in m else str(r.driver_id)),
                         escape(f"{r.side} {r.threshold}"), str(r.persistence), escape(str(r.proposal.get("kind", "—"))), escape(r.status)])
        parts.append("<h4>⑤ 판단 수정 규칙 — 발동 → 검증 → 제안 → 사람 승인</h4>"
                     + (_tbl(["규칙", "등급", "드라이버", "경계", "지속", "제안", "상태"], rows) if rows
                        else "<div class='note'>규칙 없음 — 콘솔 「G」 또는 CLI <code>mindmap rule add</code> 로 만든다 (Kill 은 A5.4)</div>"))
        evs = draft.get("rule_events") or []
        if evs:
            rows = [[escape(e["id"]), escape(e.get("rule_label", "")), escape(e.get("status", "")), escape(str(e.get("detected_at", ""))), escape(str(e.get("streak", ""))),
                     escape(str((e.get("proposal") or {}).get("kind") or "")), escape(e.get("applied") or e.get("note") or "")] for e in evs[-8:]]
            parts.append(_tbl(["이벤트", "규칙", "상태", "감지", "지속", "제안", "비고"], rows))
    sched = draft.get("evidence_schedule") or []
    if sched:
        # 2026-10-31: 날짜·무엇에 더해 **검증할 것 · 후속 조치 · 바꾸는 노드** — 일정표가 아니라 실행 목록. 판단에 안 걸리는 사건은 흐리게
        rows = []

        def _cell(t: str, dim: bool) -> str:
            return f"<span class='muted'>{escape(t)}</span>" if dim else escape(t)

        # 원문을 자르지 않는다 — 사람이 적은 규칙·검증 문장은 끝까지 읽혀야 한다. 길이는 표의 줄바꿈(CSS .sched) 이 감당한다 (2026-10-31 3차 검토)
        for x in sched[:14]:
            dim = x.get("kind") == "event" and not x.get("matters", True)
            cell = (lambda t, d=dim: _cell(t, d))  # noqa: E731
            check = x.get("check") or (f"{x['compare_at']} {x['required']}" if x.get("compare_at") else "")
            action = x.get("action") or ""
            rows.append([f"<span class='nowrap'>{cell(x['date'])}</span>", cell(x["what"]), cell(x.get("where") or ""), cell(check), cell(action),
                         cell(", ".join(x.get("affects") or []))])
        parts.append("<h4>증거 일정 (A8.1) — 날짜 · 사건 · 검증할 것 · 후속 조치 · 바꾸는 노드</h4>"
                     + _tbl(["날짜", "사건", "출처", "검증", "후속", "노드"], rows, cls="sched"))
    parts.append("<div class='note'>관측값·띠·역할·규칙은 노드 편집 폼(드라이버 observations · band · bound_bear · thesis_role; A5.6.x 규칙) 또는 콘솔 「O」「G」, CLI <code>mindmap observe / evidence / rule</code> 로 넣는다. 확률 이동·재검토는 제안이고 사람이 승인한다. 주가는 증거가 아니다.</div>")
    return "\n".join(parts)


__all__ = ["conditions_html"]
