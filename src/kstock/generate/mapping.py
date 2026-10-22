"""Claim/Issue → A 맵 매핑 (설계 v3 §2.4). 규칙 채움(rules) 뒤에 돈다. 모든 자동 노드는 claim_id 로 원문까지 되짚을 수 있다.

    A0        공통 견해·핵심 이견으로 만든 논제 초안 ("증권사 종합" 표기)
    A1.x      빈 슬롯이 있으면 상위 테마를 드라이버로 (수치가 있을 때만 current/consensus)
    A2.1.x    쟁점 — [공통 견해 | 핵심 이견 | 독립 주장 | 미해결 쟁점] 테마, 증권사별 ↑↓·– 행렬, 근거 claim_ids
    A3.6.3    리비전 사유 (revision_reason Claim 인용) 또는 '사유 확인 불가'
    A4.1~3    verified 리스크 Claim (조건부·수치 우선) — 확률·영향은 비움(사람)
    A4.4.1    조건부 리스크 Claim 에 수치가 있고 드라이버 지표와 맞으면 Rule 제안, 아니면 질문에 제안문만
    A5.1.x    촉매 Claim (날짜가 읽히는 것만)
    A8.4.x    리포트별 verified Claim 목록 (페이지·인용)
"""

from __future__ import annotations

from datetime import date

from ..models import (
    Claim,
    ClaimRole,
    ClaimSet,
    Coverage,
    Event,
    IssueDisplay,
    IssueSet,
    MapKind,
    Node,
    NodeStatus,
    Source,
)
from ..reports.digest import ReportDigest
from .issues import mechanisms_of, themes
from .rules import DRAFT_TAG, _date_in_sentence, mark_draft
from .scenarios import PENDING_TAG

SYM = {"positive": "↑", "negative": "↓", "neutral": "·", "mixed": "±"}
COV_SYM = {Coverage.partial: "◐", Coverage.unanalyzed: "?", Coverage.silent: "–"}
RULES_NOTE = "LLM 없이 생성 — Claim 기반 노드는 미생성 (mindmap claims --out … 뒤 다시; 설계 v4 V8)"


def _src(c: Claim, cs_by_id: dict[str, ClaimSet]) -> Source:
    cs = cs_by_id.get(c.report_id)
    sp = cs.span(c.span_ids[0]) if cs and c.span_ids else None
    page = f" p.{sp.page}" if sp else ""
    return Source(name=f"{c.broker} {c.report_date or ''}{page} · {c.claim_id}", url=cs.path if cs else "", retrieved_at=c.report_date)


def _quote(c: Claim, cs_by_id: dict[str, ClaimSet], n: int = 140) -> str:
    cs = cs_by_id.get(c.report_id)
    sp = cs.span(c.span_ids[0]) if cs and c.span_ids else None
    return (" ".join(sp.text.split())[:n] + ("…" if sp and len(sp.text) > n else "")) if sp else ""


def _coverage_line(issue) -> str:  # noqa: ANN001
    """증권사별 ↑↓·± (방향) — 부분 동의 ◐, 안 읽음 ?, 침묵 – (v4 V3)."""
    out = []
    for b in sorted(issue.coverage):
        cov = issue.coverage.get(b)
        if cov in COV_SYM:
            out.append(f"{b[:4]}{COV_SYM[cov]}")
        else:
            out.append(f"{b[:4]}{SYM.get(issue.directions.get(b, ''), '·')}")
    return " ".join(out)


def _tagged(c: Claim, n: int = 160) -> str:
    """[실적] [기업 발표] [기업 계획] [증권사 전망] [조건부] 접두어를 단 명제 (v4 V1)."""
    return f"[{c.tag}] {c.proposition[:n]}"


def apply_claims_to_map(m, claimsets: list[ClaimSet], issue_set: IssueSet | None, d: ReportDigest, *, today: date,  # noqa: ANN001
                        name: str | None = None) -> list[str]:
    notes: list[str] = []
    cs_by_id = {cs.report_id: cs for cs in claimsets}
    usable = [c for cs in claimsets for c in cs.usable()]
    quality = "rules" if any(cs.quality == "rules" for cs in claimsets) else ("llm" if claimsets else "none")
    m.meta.setdefault("draft", {})
    m.meta["draft"]["quality"] = quality
    m.meta["draft"]["claims"] = {cs.report_id: {"broker": cs.broker, "n": len(cs.claims), "verified": len(cs.usable()),
                                                "review": len(cs.review_queue())} for cs in claimsets}
    if not usable:
        notes.append("쓸 수 있는 Claim 이 없어 쟁점·리스크·촉매는 규칙 채움 그대로")
        return notes
    if quality == "rules":
        # v4 V8: 키워드 분류 Claim 으로는 정성 노드를 만들지 않는다 — 저품질이 정상 맵처럼 보이면 안 된다.
        for nid in ("A2.1", "A4.1", "A4.2", "A4.3", "A4.4.1", "A5.1", "A3.6.3", "A0"):
            if nid in m:
                q = m[nid].question or ""
                if RULES_NOTE not in q:
                    m[nid].question = (q + " | " + RULES_NOTE).strip(" |")[:500]
        m.meta["draft"]["rules_mode"] = True
        notes.append("⚠ 규칙 모드 — Claim 기반 노드(A0 논제·A2.1 쟁점·A3.6.3·A4 리스크·A5 촉매) 미생성. 숫자 노드만 채웠다")
        _apply_report_nodes(m, claimsets, today=today, rules_mode=True)
        return notes
    ts = themes(issue_set) if issue_set is not None else []
    # ---------------------------------------------------------------- A0 논제 초안
    common = [t for t in ts if t.display == IssueDisplay.common]
    dis = [t for t in ts if t.display == IssueDisplay.disagreement]
    if common or dis:
        head = f"[초안 · 증권사 종합] {name or m.name or ''}: "
        parts = []
        if common:
            parts.append("공통 견해 — " + ", ".join(f"{t.driver_label}{SYM.get(t.consensus_direction, '')}" for t in common[:4]))
        if dis:
            parts.append("핵심 이견 — " + ", ".join(t.driver_label for t in dis[:3]))
        body = head + "; ".join(parts) + ". 시장이 아직 반영하지 않은 것(A2.2) 과 판단은 논제의 주인이 쓴다."
        m["A0"].set(body, as_of=today, status=NodeStatus.unverified, source=d.source_all())
        mark_draft(m["A0"])
    # ---------------------------------------------------------------- A1 빈 슬롯 → 상위 테마
    slots = [n for n in m.children("A1") if n.driver is None]
    used_labels = {n.label for n in m.children("A1") if n.driver is not None}
    for t in ts:
        if not slots:
            break
        if t.driver_id in (None, "valuation_multiple", "shareholder_return", "cost") or t.driver_label in used_labels:
            continue
        if t.n_brokers < 2 and t.display != IssueDisplay.disagreement:
            continue
        n = slots.pop(0)
        n.label = t.driver_label
        n.question = f"[{t.display.value if t.display else ''}] {t.summary[:160]} — 확인지표와 현재값은 사람이 정한다"
        n.links = [f"issue:{t.issue_id}"]
        n.source = d.source_all()
        n.as_of = today
        from ..models import Driver, Measurement

        sp = t.numeric_spread
        cur = Measurement(value=None, rationale="리포트에 일관된 수치 없음")
        cons = Measurement(value=round(sp.median, 2) if sp else None, as_of=d.as_of, source=d.source_all(),
                           rationale=(f"{sp.metric} {sp.col or ''} 증권사 {len(sp.values)}곳 중앙값 (범위 {sp.min:,.1f}~{sp.max:,.1f})" if sp else "수치 없음"))
        n.unit = sp.unit if sp else None
        n.driver = Driver(current=cur, consensus=cons, my_estimate=Measurement(value=None, rationale="논제의 주인이 쓴다"),
                          check_indicator=", ".join(mm.mechanism or "" for mm in mechanisms_of(issue_set, t)[:3] if mm.mechanism) or "리포트 쟁점 참조")
        n.tags = sorted(set(n.tags) | {DRAFT_TAG, PENDING_TAG})
        notes.append(f"{n.id} 드라이버 ← 쟁점 테마 '{t.driver_label}' ({t.display.value if t.display else ''}; 수치는 사람이)")
    # ---------------------------------------------------------------- A2.1.x 쟁점
    for old in [x for x in m.nodes.values() if x.parent == "A2.1"]:
        del m.nodes[old.id]
    k = 0
    for t in ts[:8]:
        k += 1
        nid = f"A2.1.{k}"
        mech_lines = []
        for mm in mechanisms_of(issue_set, t)[:4]:
            mech_lines.append(f"{mm.mechanism or '기타'}: {_coverage_line(mm)}" + (f" — {mm.summary.split(' — ', 1)[-1][:70]}" if mm.summary else ""))
        basis = "LLM 명제 판정" if t.coverage_basis == "llm" else "방향 통계"
        partial = [b for b, v in t.coverage.items() if v == Coverage.partial]
        unread = [b for b, v in t.coverage.items() if v == Coverage.unanalyzed]
        val = (f"[{t.display.value if t.display else '?'}] {t.summary[:200]}"
               + (f"\n기준 명제: {t.reference_proposition[:120]} (판정: {basis})" if t.reference_proposition else "")
               + f"\n증권사: {_coverage_line(t)}"
               + (f" · 부분 동의 {', '.join(partial)}" if partial else "") + (f" · 안 읽음 {', '.join(unread)}" if unread else "")
               + ("\n" + "\n".join(mech_lines) if mech_lines else "")
               + ("\n미해결: " + " / ".join(t.unresolved_questions[:2]) if t.unresolved_questions else ""))
        driver_link = next((n.id for n in m.children("A1") if n.label == t.driver_label), None)
        node = Node(id=nid, map=MapKind.NEW, parent="A2.1", label=f"[{t.display.value if t.display else '?'}] {t.driver_label}", node_type="estimate",
                    value_type="text", value=val, as_of=today, source=Source(name=f"리포트 쟁점 {t.issue_id} · claims {','.join(t.claim_ids[:6])}", retrieved_at=today),
                    links=[driver_link] if driver_link else [], tags=[DRAFT_TAG], question="리포트에서 관측된 컨센서스·이견 — 현재 주가에 내재된 기대와는 다르다")
        m.add(node)
        if driver_link:
            dn = m[driver_link]
            dn.links = sorted(set(dn.links) | {nid})
    if k:
        notes.append(f"A2.1 아래 쟁점 {k}개 (공통 {len(common)} · 이견 {len(dis)})")
    # ---------------------------------------------------------------- A3.6.3 리비전 사유
    reasons = [c for c in usable if c.has_role(ClaimRole.revision_reason)]
    if reasons:
        txt = " / ".join(f"{c.broker}: {_tagged(c, 90)}" for c in reasons[:3])
        m["A3.6.3"].question = ("리비전 사유 (리포트 인용): " + txt)[:500]
        notes.append(f"A3.6.3 리비전 사유 {len(reasons)}건 (Claim)")
    else:
        m["A3.6.3"].question = (m["A3.6.3"].question + " | 리비전 사유: 리포트에서 확인 불가").strip(" |")
    # ---------------------------------------------------------------- A4 리스크
    risks = [c for c in usable if c.has_role(ClaimRole.risk) and c.direction.value == "negative"]
    risks.sort(key=lambda c: (bool(c.condition), bool(c.quant_refs), len(c.proposition)), reverse=True)
    picked: list[Claim] = []
    seen_theme: set[str] = set()
    for c in risks:
        key = c.driver_id or c.driver_label or c.claim_id
        if key in seen_theme:
            continue
        seen_theme.add(key)
        picked.append(c)
        if len(picked) == 3:
            break
    for nid, c in zip(("A4.1", "A4.2", "A4.3"), picked, strict=False):
        n = m[nid]
        n.value = {"probability": None, "impact_pct": None,
                   "text": _tagged(c) + (f" [조건: {c.condition}]" if c.condition else "") + f" ({c.broker})"}
        n.source, n.as_of, n.status = _src(c, cs_by_id), today, NodeStatus.unverified
        n.question = (n.question.split(" | 인용:")[0] + " | 인용: " + _quote(c, cs_by_id))[:400]
        n.tags = sorted(set(n.tags) | {DRAFT_TAG, PENDING_TAG})
    if picked:
        notes.append(f"A4 리스크 {len(picked)}건 ← Claim (확률·영향은 사람이)")
    # ---------------------------------------------------------------- A4.4 Kill — 후보까지만 (v4 V6)
    # 리포트의 "수율이 60% 미달이면 수익성 개선 지연" 은 논제 붕괴 조건이 아니다. 자동으로는 Risk Trigger 후보만 만들고,
    # 관측 가능 여부·갱신 주기·위반 시 행동 세 칸을 사람이 채워 확정한다 (ops.apply_kill_candidate).
    kill_src = [c for c in risks if c.condition]
    drivers = {n.label: n for n in m.children("A1") if n.driver is not None}
    cands: list[dict] = []
    for c in kill_src[:6]:
        q = c.quant_refs[0] if c.quant_refs else None
        dn = None
        if q is not None:
            dn = next((n for n in drivers.values() if (("영업이익" in n.label and q.metric == "op") or ("매출" in n.label and q.metric == "revenue")
                                                        or ("EPS" in n.label and q.metric == "eps"))), None)
        cands.append({"kpi": (q.metric if q else (c.driver_id or c.driver_label or "")), "driver_node": dn.id if dn else None,
                      "op": "<", "threshold": q.value if q else None, "unit": (q.unit if q else None),
                      "condition": c.condition, "proposition": c.proposition[:160], "broker": c.broker, "claim_id": c.claim_id,
                      "quote": _quote(c, cs_by_id, 160), "observable": None, "update_cadence": None, "action": None})
    m.meta["draft"]["kill_candidates"] = cands
    if cands:
        m["A4.4.1"].question = (m["A4.4.1"].question.split(" | 후보:")[0]
                                + f" | 후보: {len(cands)}건 — 콘솔 「Kill 후보…」/텍스트 K 에서 관측 지표·갱신 주기·위반 시 행동을 채워 확정 (자동 생성 안 함)")[:400]
    notes.append("A4.4 Kill: " + (f"조건부 리스크 후보 {len(cands)}건 — 사람이 확정" if cands else "근거 부족 — 질문으로 남김"))
    # ---------------------------------------------------------------- A5 촉매
    cats = [c for c in usable if c.has_role(ClaimRole.catalyst)]
    existing = {(n.event.date, n.event.fact[:20]) for n in m.nodes.values() if n.parent == "A5.1" and n.event}
    i = sum(1 for n in m.nodes.values() if n.parent == "A5.1")
    added = 0
    for c in cats:
        dt = _date_in_sentence(c.horizon or "", today) or _date_in_sentence(c.proposition, today)
        if dt is None or dt <= today:
            continue
        if (dt, c.proposition[:20]) in existing:
            continue
        i += 1
        if i > 6:
            break
        ev = Event(date=dt, fact=_tagged(c, 110) + f" ({c.broker})", affects=[], direction="강화" if c.direction.value == "positive" else "약화" if c.direction.value == "negative" else "중립",
                   priced_in="아니오", materiality="M", source=_src(c, cs_by_id))
        node = Node(id=f"A5.1.{i}", map=MapKind.NEW, parent="A5.1", label=c.proposition[:30], node_type="event", value_type="text", as_of=today, event=ev)
        mark_draft(node)
        m.add(node)
        added += 1
    if added:
        notes.append(f"A5.1 촉매 {added}건 ← Claim")
    # ---------------------------------------------------------------- A8.4 리포트별 주장
    _apply_report_nodes(m, claimsets, today=today, rules_mode=False)
    m.meta["draft"]["issues"] = [{"id": t.issue_id, "label": t.driver_label, "display": t.display.value if t.display else None,
                                  "direction": t.consensus_direction, "brokers": t.n_brokers, "basis": t.coverage_basis} for t in ts]
    n_h = sum(1 for c in usable if c.verified_by == "human")
    n_req = sum(1 for c in usable if c.review_required)
    m.meta["draft"]["claim_verification"] = {"machine": len(usable) - n_h, "human": n_h, "review_required": n_req}
    return notes


def _apply_report_nodes(m, claimsets: list[ClaimSet], *, today: date, rules_mode: bool) -> None:  # noqa: ANN001
    """A8.4.x — 리포트별 Claim 목록 (출처). 규칙 모드에서는 '키워드 분류·미검증' 을 머리에 적는다."""
    if "A8.4" not in m.nodes:
        m.add(Node(id="A8.4", map=MapKind.NEW, parent="A8", label="리포트별 주장 (Claim)", node_type="fact", value_type="text",
                   question="verified Claim 만 — 인용·페이지로 원문까지 되짚는다"))
    for j, cs in enumerate(claimsets, start=1):
        nid = f"A8.4.{j}"
        rows = []
        for c in cs.usable()[:12]:
            sp = cs.span(c.span_ids[0]) if c.span_ids else None
            who = "사람 확인" if c.verified_by == "human" else ("자동 검증 ⚑" if c.review_required else "자동 검증")
            rows.append(f"p.{sp.page if sp else '?'} [{c.tag}|{','.join(r.value for r in c.roles) or '-'}] {c.proposition[:120]} ({who})")
        val = ("⚠ 키워드 분류 — 미검증 초안. 맵 노드에는 쓰지 않았다\n" if rules_mode else "") + ("\n".join(rows) or "(verified Claim 없음)")
        if nid in m.nodes:
            m[nid].value = val
        else:
            m.add(Node(id=nid, map=MapKind.NEW, parent="A8.4", label=f"{cs.broker} {cs.report_date or ''}".strip(), node_type="fact", value_type="text",
                       value=val, as_of=today, source=Source(name=f"{cs.broker} 리포트 · Claim {len(cs.usable())}/{len(cs.claims)} ({cs.quality})", url=cs.path, retrieved_at=cs.report_date)))


__all__ = ["apply_claims_to_map"]
