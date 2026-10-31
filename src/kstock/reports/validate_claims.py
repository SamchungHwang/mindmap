"""Claim 3단 검증 (설계 v3 §2.2): ① 인용 존재(코드) ② 숫자 일치(코드) ③ 의미 지지(LLM 배치 판정 또는 사람).

verified = ①ok ∧ ②(ok|none) ∧ ③supported.  contradicted → rejected.  그 밖은 needs_review (검토 대기함).
사람이 검토함에서 승인/반려하면 validation.note 에 남기고 status 를 바꾼다.
"""

from __future__ import annotations

import re
from datetime import datetime

from ..generate.prompts import SUPPORT_SYSTEM, support_prompt
from ..models import Claim, ClaimSet, ClaimStatus, SupportVerdict, normalized_value
from .digest import UNIT_TO_EOK
from .extract import ReportFacts

NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
BATCH = 25


# --------------------------------------------------------------------------- ② 숫자
MANWON_RE = re.compile(r"(\d+(?:\.\d+)?)\s*만\s*원")
EOK_RE = re.compile(r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(조|억)\s*(\d+(?:,\d{3})*)?\s*(억)?\s*원?")


def _numbers_in(text: str) -> list[float]:
    """인용문의 숫자들. '53만원' → 530000, '1조 4,866억원' → 14866(억원) 과 1.4866(조원) 도 함께 넣는다."""
    out = []
    for m in NUM_RE.finditer(text):
        try:
            out.append(float(m.group().replace(",", "")))
        except ValueError:
            continue
    for m in MANWON_RE.finditer(text):
        out.append(float(m[1]) * 10000)
    for m in EOK_RE.finditer(text):
        try:
            a = float(m[1].replace(",", ""))
        except ValueError:
            continue
        if m[2] == "조":
            eok = a * 10000 + (float(m[3].replace(",", "")) if m[3] else 0.0)
            out += [eok, eok / 10000]
        else:
            out += [a, a / 10000]
    return out


def _close(a: float, b: float, tol: float = 0.006) -> bool:
    """부호는 무시한다 — '1.5조원의 영업적자' 를 -1.5 로 적는 것은 표기 차이다."""
    a, b = abs(a), abs(b)
    return abs(a - b) <= tol * max(b, 1e-9)


def _table_values(f: ReportFacts | None) -> list[float]:
    if f is None:
        return []
    vals: list[float] = []
    for table in (f.estimates, f.quarterly):
        for cols in table.values():
            vals += [v for v in cols.values() if v is not None]
    return vals


def check_numbers(claim: Claim, cs: ClaimSet, facts: ReportFacts | None, *, fulltext: str | None = None) -> str:
    """quant_refs 의 값이 인용문 안의 숫자이거나 표의 값(단위 환산 허용: 억·십억·조) 이면 ok. 없으면 none, 하나라도 못 찾으면 mismatch.

    명제(proposition) 에 적히지 않은 수치가 quant 에 붙어 오는 경우(LLM 이 표의 정밀값 100.7 을 '100조원' 명제에 붙임, 목표가를
    밸류 명제에 끼움) 는 명제가 주장하는 숫자가 아니므로 불일치로 치지 않고 quant 에서 뺀다 (note 에 남김)."""
    if not claim.quant_refs:
        return "none"
    prop_nums = _numbers_in(claim.proposition)
    span_nums: list[float] = []
    for sid in claim.span_ids:
        sp = cs.span(sid)
        if sp:
            span_nums += _numbers_in(sp.text)
    table = _table_values(facts)
    table_unit = UNIT_TO_EOK.get((facts.unit if facts else "") or "", 10.0)
    kept, dropped, outside, mismatch = [], [], [], False
    for q in claim.quant_refs:
        v = q.value
        in_evidence = any(_close(v, x) for x in span_nums)
        if not in_evidence and table:
            # 표 단위 ↔ 주장 단위 환산 (예: 표 십억원, 주장 조원)
            qf = UNIT_TO_EOK.get(q.unit or "", None)
            cands = [v]
            if qf and table_unit:
                cands.append(v * qf / table_unit)
            in_evidence = any(_close(c, x) for c in cands for x in table)
        if in_evidence:
            kept.append(q)                                       # 인용이나 표에 있다 — 근거 있는 수치
        elif any(_close(v, x) for x in prop_nums):
            # 명제가 말하는 숫자인데 인용·표에 없다. 리포트 본문(fulltext) 어딘가(사업부 표 등) 에 있으면 받되 ⚑ 를 단다 (v4 V2)
            if fulltext and any(_close(v, x) for x in _numbers_in(fulltext)):
                kept.append(q)
                outside.append(q)
            else:
                kept.append(q)
                mismatch = True                                  # 리포트 어디에도 없는 숫자
        else:
            dropped.append(q)                                    # 명제도 안 말하고 근거도 없는 수치 — LLM 이 끼운 잡음
    if dropped:
        shown = ", ".join(f"{q.value:g}{q.unit or ''}" for q in dropped[:3])
        claim.validation.note = (claim.validation.note + " · " if claim.validation.note else "") + f"명제에 없는 수치 {len(dropped)}개 제외: {shown}"
        claim.quant_refs = kept
    if outside:
        shown = ", ".join(f"{q.value:g}{q.unit or ''}" for q in outside[:3])
        claim.validation.note = (claim.validation.note + " · " if claim.validation.note else "") + f"인용 밖 본문(표) 수치 {len(outside)}개: {shown} — 확인 권고"
        _add_reason(claim, REASON_OUTSIDE)
    if mismatch:
        return "mismatch"
    return "ok" if kept else "none"


# --------------------------------------------------------------------------- ③ 의미 (LLM 배치)
def semantic_check(cs: ClaimSet, filler, *, batch: int = BATCH, only_unchecked: bool = True) -> dict[str, str]:  # noqa: ANN001
    """인용이 있는 Claim 을 batch 개씩 LLM 에 물어 verdict 를 적용한다. 돌려주는 것: claim_id → verdict."""
    from ..generate.llm_fill import complete_json

    def _quote_of(c: Claim) -> str:
        parts = [" ".join(cs.span(sid).text.split()) for sid in c.span_ids if cs.span(sid)]
        return " … ".join(p for p in parts if p)      # 인용이 여럿이면 전부 — 명제가 여러 문장에 걸칠 수 있다

    items = [(c.claim_id, c.proposition, _quote_of(c))
             for c in cs.claims if c.span_ids and (not only_unchecked or c.validation.support == SupportVerdict.unchecked)]
    items = [x for x in items if x[2]]
    out: dict[str, str] = {}
    for i in range(0, len(items), batch):
        part = items[i:i + batch]
        try:
            ans = complete_json(filler, SUPPORT_SYSTEM, support_prompt(part), retries=1)
        except Exception as e:  # noqa: BLE001
            cs.notes.append(f"의미 검증 배치 {i // batch + 1} 실패: {type(e).__name__}: {str(e)[:100]}")
            continue
        for row in ans.get("verdicts") or []:
            if not isinstance(row, dict):
                continue
            cid, v = str(row.get("id", "")), str(row.get("verdict", "")).strip().lower()
            if v not in ("supported", "contradicted", "insufficient"):
                continue
            out[cid] = v
            c = next((x for x in cs.claims if x.claim_id == cid), None)
            if c is not None:
                c.validation.support = SupportVerdict(v)
                why = str(row.get("why") or "")[:120]
                if why:
                    c.validation.note = (c.validation.note + " · " if c.validation.note else "") + f"의미: {why}"
    return out


# --------------------------------------------------------------------------- 상태 확정
def finalize(claim: Claim, *, require_semantic: bool = True) -> ClaimStatus:
    v = claim.validation
    if claim.verified_by == "human" and claim.status in (ClaimStatus.verified, ClaimStatus.rejected):
        return claim.status                                # 사람이 정한 것은 기계가 되돌리지 않는다
    if v.support == SupportVerdict.contradicted:
        claim.status = ClaimStatus.rejected
        claim.verified_by = ""
    elif v.quote == "ok" and v.numbers in ("ok", "none") and (v.support == SupportVerdict.supported or (not require_semantic and v.support == SupportVerdict.unchecked)):
        claim.status = ClaimStatus.verified
        claim.verified_by = "machine"
    else:
        claim.status = ClaimStatus.needs_review
        claim.verified_by = ""
    return claim.status


# --------------------------------------------------------------------------- v4 V2: 자동 통과해도 사람이 봐야 하는 것
DECISION_METRICS = ("op", "revenue", "eps", "target_price", "np", "opm")
CONFLICT_REL = 0.15
FORWARD_COL = re.compile(r"^(?:\d{4}E|\dQ\d{2}E)$")
REASON_UNCORROBORATED = "판단 지표 수치(표에 없음)"
REASON_CONFLICT = "증권사 간 수치 차이"
REASON_CONDITIONAL = "조건부 리스크"
REASON_ANALYST_FACT = "애널리스트가 사실로 단정"
REASON_OUTSIDE = "인용 밖 본문 수치"
_REASONS_LOCAL = (REASON_UNCORROBORATED, REASON_OUTSIDE)        # 리포트 1건 안에서만 알 수 있는 이유 — 묶음 재계산 때 보존


def _add_reason(claim: Claim, reason: str) -> None:
    reasons = [r for r in (claim.review_reason or "").split(" · ") if r]
    if not any(r.startswith(reason) for r in reasons):
        reasons.append(reason)
    claim.review_reason = " · ".join(reasons)
    claim.review_required = True


def _is_forward(q) -> bool:  # noqa: ANN001
    return q.metric == "target_price" or bool(q.col and FORWARD_COL.match(q.col))


def _corroborated(q, facts: ReportFacts | None) -> bool:  # noqa: ANN001
    """그 리포트의 표(추정치·분기)·목표가에 같은 숫자가 있나 (단위 환산 포함)."""
    if facts is None:
        return False
    if q.metric == "target_price":
        return facts.target_price is not None and _close(q.value, facts.target_price)
    # 같은 지표의 표 값만 — 'MX 1.0조' 가 PBR 1.0 과 맞아떨어지는 식의 우연을 막는다
    table = [v for tbl in (facts.estimates, facts.quarterly) for v in (tbl.get(q.metric) or {}).values() if v is not None]
    if not table:
        return False
    table_unit = UNIT_TO_EOK.get(facts.unit or "", 10.0)
    cands = [q.value]
    qf = UNIT_TO_EOK.get(q.unit or "", None)
    if qf and table_unit:
        cands.append(q.value * qf / table_unit)
    return any(_close(c, x) for c in cands for x in table)


def company_level(claim: Claim, q, facts: ReportFacts | None) -> bool:  # noqa: ANN001
    """이 수치가 전사 수준인가 — 리포트 표(facts) 가 있으면 '표에 있는 값' 으로, 없으면 사업부 문장 휴리스틱으로."""
    if facts is not None and (_table_values(facts) or facts.target_price is not None):
        return _corroborated(q, facts)
    return not claim.is_segment


def mark_review_required(claimsets: list[ClaimSet], *, facts_by_report: dict[str, ReportFacts] | None = None) -> int:
    """규칙으로 review_required 를 단다 (상태는 바꾸지 않는다 — 초안에는 쓰되 ⚑ 로 보인다). 설계 v4 V2.
    ① 전사 수준·미래 기간(…E) 의 판단 지표 수치가 그 리포트 표에도 없을 때 (facts 가 있을 때만 판정; 없으면 앞선 판정 보존)
    ② 같은 (metric, col) 의 **전사 수준·미래 기간** 수치가 다른 증권사와 15% 넘게 다를 때 (단위 환산; 사업부 수치·목표가는 제외 —
       목표가 차이는 A3.5.4 분포가 보여 준다)
    ③ 조건부(conditional) 리스크 Claim
    ④ 애널리스트가 '사실(fact/realized)' 로 단정한 논거 Claim
    ⑤ (check_numbers 가 단) 인용 밖 본문 수치 — 보존"""
    cells: dict[tuple[str, str | None], dict[str, float]] = {}
    for cs in claimsets:
        facts = (facts_by_report or {}).get(cs.report_id)
        for c in cs.claims:
            for q in c.quant_refs:
                if q.metric in DECISION_METRICS and q.metric != "target_price" and _is_forward(q) and company_level(c, q, facts):
                    v, _ = normalized_value(q.value, q.unit)
                    cells.setdefault((q.metric, q.col), {})[c.broker or cs.broker or "?"] = v
    n = 0
    for cs in claimsets:
        facts = (facts_by_report or {}).get(cs.report_id)
        for c in cs.claims:
            prev = [r for r in (c.review_reason or "").split(" · ") if r]
            reasons: list[str] = [r for r in prev if any(r.startswith(k) for k in _REASONS_LOCAL)]
            if facts is not None:
                reasons = [r for r in reasons if not r.startswith(REASON_UNCORROBORATED)]
                fwd = [q for q in c.quant_refs if q.metric in DECISION_METRICS and _is_forward(q)]
                if fwd and not c.is_segment and not any(_corroborated(q, facts) for q in fwd):
                    reasons.append(REASON_UNCORROBORATED)
            if True:
                for q in c.quant_refs:
                    if q.metric not in DECISION_METRICS or q.metric == "target_price" or not _is_forward(q) or not company_level(c, q, facts):
                        continue
                    by = cells.get((q.metric, q.col), {})
                    me = c.broker or cs.broker or "?"
                    mine, _ = normalized_value(q.value, q.unit)
                    others = [v for b, v in by.items() if b != me]
                    if others and any(abs(mine - o) > CONFLICT_REL * max(abs(o), 1e-9) for o in others):
                        reasons.append(f"{REASON_CONFLICT} >{CONFLICT_REL:.0%} ({q.metric} {q.col or ''})".replace(" )", ")"))
                        break
            if c.certainty.value == "conditional" and c.has_role("risk"):
                reasons.append(REASON_CONDITIONAL)
            if (c.epistemic_type.value == "fact" or c.certainty.value == "realized") and c.asserted_by.value == "analyst" and c.has_role("thesis"):
                reasons.append(REASON_ANALYST_FACT)
            if c.verified_by == "human":
                reasons = []                                      # 사람이 봤다
            c.review_required = bool(reasons)
            c.review_reason = " · ".join(dict.fromkeys(reasons))
            n += int(bool(reasons))
    return n


def run_all(cs: ClaimSet, facts: ReportFacts | None, filler=None, *, semantic: bool = True, fulltext: str | None = None) -> dict[str, int]:  # noqa: ANN001
    """①②③ 를 차례로 돌리고 상태를 확정한다. filler 가 없거나 semantic=False 면 ③ 은 건너뛰고(unchecked) verified 는 ①② 만으로 — 그러나
    require_semantic=True 가 기본이라 그 경우 needs_review 로 남는다. 규칙 Claim(quality='rules') 은 ③ 없이 verified 를 허용한다."""
    for c in cs.claims:
        if c.validation.quote == "unchecked":
            c.validation.quote = "ok" if c.span_ids else "miss"
        c.review_required, c.review_reason = False, ""           # 검증을 다시 하면 이유도 다시
        c.validation.numbers = check_numbers(c, cs, facts, fulltext=fulltext)
    if semantic and filler is not None and cs.quality != "rules":
        semantic_check(cs, filler)
    counts = {"verified": 0, "needs_review": 0, "rejected": 0}
    for c in cs.claims:
        if c.inferred:
            c.status = ClaimStatus.needs_review
            counts["needs_review"] += 1
            continue
        st = finalize(c, require_semantic=(cs.quality != "rules"))
        counts[st.value] += 1
    n_req = mark_review_required([cs], facts_by_report={cs.report_id: facts} if facts is not None else None)
    cs.notes.append(f"검증: verified {counts['verified']} · needs_review {counts['needs_review']} · rejected {counts['rejected']}"
                    + (f" · 확인 권고 {n_req}" if n_req else ""))
    return counts


# --------------------------------------------------------------------------- 검토 대기함 (사람)
def review(cs: ClaimSet, claim_id: str, decision: str, *, who: str = "user", note: str = "") -> Claim:
    """decision: approve | reject. 승인은 verified (의미 판정을 사람이 대신), 반려는 rejected. verified_by="human".
    자동 판정이 insufficient 였던 것을 승인할 때는 note(승인 이유) 가 필수다 (v4 V2)."""
    c = next((x for x in cs.claims if x.claim_id == claim_id), None)
    if c is None:
        raise KeyError(claim_id)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    if decision == "approve":
        if c.validation.support == SupportVerdict.insufficient and not note.strip():
            raise ValueError(f"{claim_id}: 자동 판정이 '근거 불충분' 이었다 — 승인 이유(note) 를 적어야 한다")
        c.status = ClaimStatus.verified
        c.validation.support = SupportVerdict.supported
        c.inferred = False
    elif decision == "reject":
        c.status = ClaimStatus.rejected
    else:
        raise ValueError(decision)
    c.verified_by = "human"
    c.review_required = False
    c.validation.note = (c.validation.note + " · " if c.validation.note else "") + f"{who} {decision} {stamp}" + (f": {note}" if note else "")
    return c


__all__ = ["check_numbers", "semantic_check", "finalize", "run_all", "review", "mark_review_required", "company_level", "BATCH", "DECISION_METRICS",
           "REASON_UNCORROBORATED", "REASON_CONFLICT", "REASON_CONDITIONAL", "REASON_ANALYST_FACT", "REASON_OUTSIDE"]
