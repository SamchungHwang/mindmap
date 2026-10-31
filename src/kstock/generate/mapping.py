"""Claim/Issue → A 맵 매핑 (설계 v3 §2.4). 규칙 채움(rules) 뒤에 돈다. 모든 자동 노드는 claim_id 로 원문까지 되짚을 수 있다.

    A0        공통 견해·핵심 이견으로 만든 논제 초안 ("증권사 종합" 표기)
    A1.x      빈 슬롯이 있으면 상위 테마를 드라이버로 (수치가 있을 때만 current/consensus)
    A2.1.x    쟁점 — [공통 견해 | 핵심 이견 | 독립 주장 | 미해결 쟁점] 테마, 증권사별 ↑↓·– 행렬, 근거 claim_ids
    A3.5.3    리비전 사유 (revision_reason Claim 인용) 또는 '사유 확인 불가'
    A5.1~3    verified 리스크 Claim (조건부·수치 우선) — 확률·영향은 비움(사람)
    A5.4.1    조건부 리스크 Claim 에 수치가 있고 드라이버 지표와 맞으면 Rule 제안, 아니면 질문에 제안문만
    A6.1.x    촉매 Claim (날짜가 읽히는 것만)
    A9.4.x    리포트별 verified Claim 목록 (페이지·인용)
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
from .rules import DRAFT_TAG, mark_draft
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
        for nid in ("A2.1", "A5.1", "A5.2", "A5.3", "A5.4.1", "A6.1", "A3.5.3", "A0"):
            if nid in m:
                q = m[nid].question or ""
                if RULES_NOTE not in q:
                    m[nid].question = (q + " | " + RULES_NOTE).strip(" |")[:500]
        m.meta["draft"]["rules_mode"] = True
        notes.append("⚠ 규칙 모드 — Claim 기반 노드(A0 논제·A2.1 쟁점·A3.5.3·A5 리스크·A6 촉매) 미생성. 숫자 노드만 채웠다")
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
    # A1 원인 드라이버는 generate.cause.fill_cause_drivers 가 채운다 (설계 v5 — 결과 지표는 A3.1, 테마 → A1 자동 배정은 거기서)
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
        from .cause import driver_node_for

        driver_link = driver_node_for(m, t.driver_id, t.driver_label)
        node = Node(id=nid, map=MapKind.NEW, parent="A2.1", label=f"[{t.display.value if t.display else '?'}] {t.driver_label}", node_type="estimate",
                    value_type="text", value=val, as_of=today, source=Source(name=f"리포트 쟁점 {t.issue_id} · claims {','.join(t.claim_ids[:6])}", retrieved_at=today),
                    links=[driver_link] if driver_link else [], tags=[DRAFT_TAG], question="리포트에서 관측된 컨센서스·이견 — 현재 주가에 내재된 기대와는 다르다")
        m.add(node)
        if driver_link:
            dn = m[driver_link]
            dn.links = sorted(set(dn.links) | {nid})
    if k:
        notes.append(f"A2.1 아래 쟁점 {k}개 (공통 {len(common)} · 이견 {len(dis)})")
    # ---------------------------------------------------------------- A3.5.3 리비전 사유
    reasons = [c for c in usable if c.has_role(ClaimRole.revision_reason)]
    if reasons:
        txt = " / ".join(f"{c.broker}: {_tagged(c, 90)}" for c in reasons[:3])
        m["A3.5.3"].question = ("리비전 사유 (리포트 인용): " + txt)[:500]
        notes.append(f"A3.5.3 리비전 사유 {len(reasons)}건 (Claim)")
    else:
        m["A3.5.3"].question = (m["A3.5.3"].question + " | 리비전 사유: 리포트에서 확인 불가").strip(" |")
    # ---------------------------------------------------------------- A5 리스크
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
    for nid, c in zip(("A5.1", "A5.2", "A5.3"), picked, strict=False):
        n = m[nid]
        n.value = {"probability": None, "impact_pct": None,
                   "text": _tagged(c) + (f" [조건: {c.condition}]" if c.condition else "") + f" ({c.broker})"}
        n.source, n.as_of, n.status = _src(c, cs_by_id), today, NodeStatus.unverified
        n.question = (n.question.split(" | 인용:")[0] + " | 인용: " + _quote(c, cs_by_id))[:400]
        n.tags = sorted(set(n.tags) | {DRAFT_TAG, PENDING_TAG})
    if picked:
        notes.append(f"A5 리스크 {len(picked)}건 ← Claim (확률·영향은 사람이)")
    # ---------------------------------------------------------------- A5.4 Kill — 후보까지만 (v4 V6)
    # 리포트의 "수율이 60% 미달이면 수익성 개선 지연" 은 논제 붕괴 조건이 아니다. 자동으로는 Risk Trigger 후보만 만들고,
    # 관측 가능 여부·갱신 주기·위반 시 행동 세 칸을 사람이 채워 확정한다 (ops.apply_kill_candidate).
    kill_src = [c for c in risks if c.condition]
    drivers = {n.label: n for n in (m.children("A1") + (m.children("A3.1") if "A3.1" in m else [])) if n.driver is not None}
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
        m["A5.4.1"].question = (m["A5.4.1"].question.split(" | 후보:")[0]
                                + f" | 후보: {len(cands)}건 — 콘솔 「Kill 후보…」/텍스트 K 에서 관측 지표·갱신 주기·위반 시 행동을 채워 확정 (자동 생성 안 함)")[:400]
    notes.append("A5.4 Kill: " + (f"조건부 리스크 후보 {len(cands)}건 — 사람이 확정" if cands else "근거 부족 — 질문으로 남김"))
    # ---------------------------------------------------------------- A6 촉매
    # 2026-10-31: **회사가 발표·계획한 것만 이벤트**가 된다 (certainty announced/planned 또는 asserted_by company). 날짜뿐 아니라
    # 기간(「1H27 55MW」 「2028년까지 200MW」) 도 읽고, 제목은 시점이 든 절만 (긴 전망 문장을 노드 제목으로 쓰지 않는다).
    # 증권사 전망 문장은 이벤트가 아니라 **증거**다 — 원인 드라이버가 채워진 뒤 `apply_analyst_catalysts` 가 bull/bear/precheck 로 붙인다.
    from ..models.claims import Certainty as ClaimCertainty
    from .catalysts import EventCertainty, event_program, find_stage, merge_into_map, parse_when, time_clause
    from .event_text import PLAN_WORDS

    incoming: list[Event] = []
    skipped_delivery: list[str] = []
    seen_fact: set[str] = set()
    for c in [c for c in usable if c.has_role(ClaimRole.catalyst)]:
        company = c.certainty in (ClaimCertainty.announced, ClaimCertainty.planned) or c.asserted_by.value == "company"
        if not company:
            continue
        anchor = c.effective_as_of or today
        key = c.proposition[:40]
        if key in seen_fact:
            continue
        seen_fact.add(key)
        ms = milestone_events(c, cs_by_id, today=today, sector=(m.meta.get("draft") or {}).get("sector"))
        if ms:
            incoming += ms
            continue
        w = parse_when(c.proposition, today, anchor=anchor) or parse_when(c.horizon or "", today, anchor=anchor)
        if w is None or w.date <= today:
            continue
        if w.role == "delivery" and not PLAN_WORDS.search(c.proposition):
            # 납기·출하 시점만 있는 문장(「가스터빈은 2031년 출하분까지 예약·협상 진행 중」) 은 사건이 아니다 — 2031 에 노드가 서지 않게 (2026-10-31 두산에너빌리티)
            skipped_delivery.append(f"{c.claim_id} {w.period or w.date.isoformat()} {c.proposition[:40]}")
            continue
        # 접두어는 이벤트 등급과 같은 말을 쓴다 — 「회사 계획」 (Claim 의 [기업 계획] 표기는 A9.4 에 남긴다)
        prefix = "회사 발표" if c.certainty == ClaimCertainty.announced else "회사 계획"
        # 사업명(X-energy · 폴란드 AP1000 …) 을 적어 두면 본문 이벤트와 같은 사건인지 `same_event` 가 주체로 가른다 (2026-10-31 두산에너빌리티)
        delivery = (w.delivery.period or w.delivery.date.isoformat()[:7]) if w.delivery else None
        incoming.append(Event(date=w.date, period=w.period, fact=f"[{prefix}] {time_clause(c.proposition, w)}", affects=[], direction="미정",
                              priced_in="미판단", materiality="H", certainty=EventCertainty.회사계획인용, source=_src(c, cs_by_id),
                              program=event_program(c.proposition), stage=find_stage(c.proposition), delivery_period=delivery,
                              evidence=[f"(·) [{c.broker}] {' '.join(c.proposition.split())[:140]}"], event_id=f"claim:{c.claim_id}"))
        if len(incoming) >= 6:
            break
    if incoming:
        merge_notes = merge_into_map(m, incoming[:10], today=today)
        notes.append(f"A6.1 회사 계획(리포트 인용) {len(incoming)}건 ← Claim" + (" · " + "; ".join(merge_notes) if merge_notes else ""))
    if skipped_delivery:
        notes.append("A6.1 납기만 있는 Claim 은 사건이 아니라 뺌: " + "; ".join(skipped_delivery))
    # ---------------------------------------------------------------- A9.4 리포트별 주장
    _apply_report_nodes(m, claimsets, today=today, rules_mode=False)
    m.meta["draft"]["issues"] = [{"id": t.issue_id, "label": t.driver_label, "display": t.display.value if t.display else None,
                                  "direction": t.consensus_direction, "brokers": t.n_brokers, "basis": t.coverage_basis} for t in ts]
    n_h = sum(1 for c in usable if c.verified_by == "human")
    n_req = sum(1 for c in usable if c.review_required)
    m.meta["draft"]["claim_verification"] = {"machine": len(usable) - n_h, "human": n_h, "review_required": n_req}
    return notes


def _apply_report_nodes(m, claimsets: list[ClaimSet], *, today: date, rules_mode: bool) -> None:  # noqa: ANN001
    """A9.4.x — 리포트별 Claim 목록 (출처). 규칙 모드에서는 '키워드 분류·미검증' 을 머리에 적는다."""
    if "A9.4" not in m.nodes:
        m.add(Node(id="A9.4", map=MapKind.NEW, parent="A9", label="리포트별 주장 (Claim)", node_type="fact", value_type="text",
                   question="verified Claim 만 — 인용·페이지로 원문까지 되짚는다"))
    for j, cs in enumerate(claimsets, start=1):
        nid = f"A9.4.{j}"
        rows = []
        for c in cs.usable()[:12]:
            sp = cs.span(c.span_ids[0]) if c.span_ids else None
            who = "사람 확인" if c.verified_by == "human" else ("자동 검증 ⚑" if c.review_required else "자동 검증")
            rows.append(f"p.{sp.page if sp else '?'} [{c.tag}|{','.join(r.value for r in c.roles) or '-'}] {c.proposition[:120]} ({who})")
        val = ("⚠ 키워드 분류 — 미검증 초안. 맵 노드에는 쓰지 않았다\n" if rules_mode else "") + ("\n".join(rows) or "(verified Claim 없음)")
        if nid in m.nodes:
            m[nid].value = val
        else:
            m.add(Node(id=nid, map=MapKind.NEW, parent="A9.4", label=f"{cs.broker} {cs.report_date or ''}".strip(), node_type="fact", value_type="text",
                       value=val, as_of=today, source=Source(name=f"{cs.broker} 리포트 · Claim {len(cs.usable())}/{len(cs.claims)} ({cs.quality})", url=cs.path, retrieved_at=cs.report_date)))


_CAPACITY_UNITS = ("MW", "GW", "MWh", "GWh", "기", "대", "척", "호기")


def milestone_events(c: Claim, cs_by_id: dict[str, ClaimSet], *, today: date, sector: str | None) -> list[Event]:
    """한 Claim 의 수치 칸에 **기간이 붙은 용량·수량**이 있으면 마일스톤 이벤트로 쪼갠다 (2026-10-31).

    하나증권 c9 「2027년 상반기 55MW, 2027년 말 100MW, 2028년 말 200MW」 는 quant_refs 에 (capacity_1h27 55 MW 1H27) ·
    (capacity_2027 100 MW 2027E) · (capacity_2028 200 MW 2028E) 로 이미 갈라져 있다. 문장 하나를 이벤트 하나로 만들면 첫 기간만
    남고 나머지는 사라진다. 같은 사업(`project` = 드라이버 id) 으로 묶어 2027~2028 진척을 한 줄로 본다.
    """
    from ..models.claims import Certainty as ClaimCertainty
    from .catalysts import EventCertainty, parse_when
    from .kpi_catalog import _DRIVER_BY_ID, normalize_driver

    out: list[Event] = []
    did = c.driver_id if c.driver_id in _DRIVER_BY_ID else None
    did = normalize_driver(c.proposition, sector=sector) or normalize_driver(c.proposition.replace(" ", ""), sector=sector) or did
    label = _DRIVER_BY_ID[did].label if did in _DRIVER_BY_ID else (c.driver_label or "")
    company = (c.certainty in (ClaimCertainty.announced, ClaimCertainty.planned, ClaimCertainty.realized) or c.asserted_by.value == "company"
               or any(w in c.proposition for w in ("목표", "계획", "약정", "예정")))
    mark = {"positive": "(+)", "negative": "(−)"}.get(c.direction.value, "(·)")
    sentence = f"{mark} [{c.broker}] {' '.join(c.proposition.split())[:140]}"
    for q in c.quant_refs:
        metric = (q.metric or "").strip()
        unit = (q.unit or "").strip()
        if q.value is None or not (metric.startswith("capacity") or unit in _CAPACITY_UNITS):
            continue
        w = parse_when(q.col or "", today) or parse_when(metric.replace("_", " "), today)
        if w is None or w.date <= today:
            continue
        # 리포트가 「회사 계획」 이라 적어도 회사 원문을 본 건 아니다 — 「회사 계획 (리포트 인용)」. 「회사 목표」 는 DART·회사 자료로 확인된 것만 (2026-10-31)
        fact = f"[{'회사 계획' if company else '증권사 전망'}] {label or '사업'} {q.value:g}{unit} ({w.period or w.date.isoformat()})"
        ev = Event(date=w.date, period=w.period, fact=fact, affects=[], direction="미정", priced_in="미판단", materiality="H" if company else "M",
                   certainty=EventCertainty.회사계획인용 if company else EventCertainty.리포트전망, source=_src(c, cs_by_id),
                   evidence=[sentence], project=did, event_id=f"{did or 'claim'}:{metric or 'capacity'}:{(w.period or w.date.isoformat()).lower()}")
        if company:
            # 사전 확인은 **주제별** — AI 인프라(MW·GPU) 와 일반 증설·LTA 비중은 봐야 할 것이 다르다 (2026-10-31 삼성전자: LTA 목표에 GPU 가동률이 붙었다)
            from .catalysts import default_precheck

            ev = ev.model_copy(update={"precheck": default_precheck(ev)})
        out.append(ev)
    return out


def apply_analyst_catalysts(m, claimsets: list[ClaimSet], *, today: date) -> list[str]:  # noqa: ANN001
    """증권사 전망 촉매 Claim → 기존 이벤트의 **증거**(bull·bear·precheck). 붙일 이벤트가 없고 시점이 읽히면 「리포트 전망」
    이벤트를 하나 만든다 — 제목은 시점이 든 절만, 문장 전체는 그 이벤트의 증거 칸에. 원인 드라이버(A1) 가 채워진 뒤에 부른다
    (같은 드라이버를 가리키는 이벤트에 붙이려면 driver_id → A1 노드 연결이 있어야 한다)."""
    from ..models.claims import Certainty as ClaimCertainty
    from .catalysts import EventCertainty, attach_evidence, merge_into_map, parse_when, time_clause
    from .cause import driver_node_for

    cs_by_id = {cs.report_id: cs for cs in claimsets}
    usable = [c for cs in claimsets for c in cs.usable()]
    attached, standalone = 0, []
    for c in [c for c in usable if c.has_role(ClaimRole.catalyst)]:
        company = c.certainty in (ClaimCertainty.announced, ClaimCertainty.planned) or c.asserted_by.value == "company"
        if company:
            continue
        anchor = c.effective_as_of or today
        ms = milestone_events(c, cs_by_id, today=today, sector=(m.meta.get("draft") or {}).get("sector"))
        if ms:
            standalone += ms
            continue
        w = parse_when(c.proposition, today, anchor=anchor) or parse_when(c.horizon or "", today, anchor=anchor)
        future = w if (w is not None and w.date > today) else None
        dn = driver_node_for(m, c.driver_id, c.driver_label)
        text = f"[{c.broker}] {' '.join(c.proposition.split())}"
        nid = attach_evidence(m, when=future, driver_node=dn, text=text, direction=c.direction.value, today=today)
        if nid:
            attached += 1
            continue
        if future is None:
            continue
        mark = {"positive": "(+)", "negative": "(−)"}.get(c.direction.value, "(·)")
        ev = Event(date=future.date, period=future.period, fact=f"[증권사 전망] {time_clause(c.proposition, future)} ({c.broker})", affects=[dn] if dn else [],
                   direction="미정", priced_in="미판단", materiality="M", certainty=EventCertainty.리포트전망, source=_src(c, cs_by_id),
                   event_id=f"claim:{c.claim_id}", evidence=[f"{mark} {text[:140]}"])
        standalone.append(ev)
    notes: list[str] = []
    if standalone:
        merge_notes = merge_into_map(m, standalone[:8], today=today)
        notes.append(f"A6.1 리포트 전망·마일스톤 {min(len(standalone), 8)}건 ← Claim" + (" · " + "; ".join(merge_notes) if merge_notes else ""))
    if attached:
        notes.append(f"A6.1 증거 {attached}건 ← 증권사 촉매 Claim (기존 이벤트의 상방·하방·사전 확인에)")
    return notes


__all__ = ["apply_claims_to_map", "apply_analyst_catalysts", "milestone_events"]
