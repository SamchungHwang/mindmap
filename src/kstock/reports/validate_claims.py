"""Claim 3단 검증 (설계 v3 §2.2): ① 인용 존재(코드) ② 숫자 일치(코드) ③ 의미 지지(LLM 배치 판정 또는 사람).

verified = ①ok ∧ ②(ok|none) ∧ ③supported.  contradicted → rejected.  그 밖은 needs_review (검토 대기함).
사람이 검토함에서 승인/반려하면 validation.note 에 남기고 status 를 바꾼다.
"""

from __future__ import annotations

import re
from datetime import datetime

from ..generate.prompts import SUPPORT_SYSTEM, support_prompt
from ..models import Claim, ClaimSet, ClaimStatus, SupportVerdict
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


def check_numbers(claim: Claim, cs: ClaimSet, facts: ReportFacts | None) -> str:
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
    kept, dropped, mismatch = [], [], False
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
            kept.append(q)
            mismatch = True                                      # 명제가 말하는 숫자인데 인용·표 어디에도 없다
        else:
            dropped.append(q)                                    # 명제도 안 말하고 근거도 없는 수치 — LLM 이 끼운 잡음
    if dropped:
        shown = ", ".join(f"{q.value:g}{q.unit or ''}" for q in dropped[:3])
        claim.validation.note = (claim.validation.note + " · " if claim.validation.note else "") + f"명제에 없는 수치 {len(dropped)}개 제외: {shown}"
        claim.quant_refs = kept
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


def mark_review_required(claimsets: list[ClaimSet]) -> int:
    """규칙으로 review_required 를 단다 (상태는 바꾸지 않는다 — 초안에는 쓰되 ⚑ 로 보인다).
    ① 목표가·A1 지표(op/revenue/eps/target_price/np/opm) 에 직접 쓰이는 수치 Claim
    ② 같은 (metric, col) 수치가 다른 증권사와 15% 넘게 다른 Claim
    ③ 조건부(conditional) 리스크 Claim
    ④ 애널리스트가 '사실(fact/realized)' 로 단정한 Claim (기업 발표가 아닌데 fact)"""
    cells: dict[tuple[str, str | None], dict[str, float]] = {}
    for cs in claimsets:
        for c in cs.claims:
            for q in c.quant_refs:
                cells.setdefault((q.metric, q.col), {})[c.broker or cs.broker or "?"] = q.value
    n = 0
    for cs in claimsets:
        for c in cs.claims:
            reasons: list[str] = []
            if any(q.metric in DECISION_METRICS for q in c.quant_refs):
                reasons.append("판단 지표 수치")
            for q in c.quant_refs:
                by = cells.get((q.metric, q.col), {})
                if len(by) >= 2:
                    others = [v for b, v in by.items() if b != (c.broker or cs.broker or "?")]
                    if others and any(abs(q.value - o) > CONFLICT_REL * max(abs(o), 1e-9) for o in others):
                        reasons.append(f"증권사 간 수치 차이 >{CONFLICT_REL:.0%} ({q.metric} {q.col or ''})".strip())
                        break
            if c.certainty.value == "conditional" and c.has_role("risk"):
                reasons.append("조건부 리스크")
            if (c.epistemic_type.value == "fact" or c.certainty.value == "realized") and c.asserted_by.value == "analyst" and c.has_role("thesis"):
                reasons.append("애널리스트가 사실로 단정")
            c.review_required = bool(reasons)
            c.review_reason = " · ".join(dict.fromkeys(reasons))
            n += int(bool(reasons))
    return n


def run_all(cs: ClaimSet, facts: ReportFacts | None, filler=None, *, semantic: bool = True) -> dict[str, int]:  # noqa: ANN001
    """①②③ 를 차례로 돌리고 상태를 확정한다. filler 가 없거나 semantic=False 면 ③ 은 건너뛰고(unchecked) verified 는 ①② 만으로 — 그러나
    require_semantic=True 가 기본이라 그 경우 needs_review 로 남는다. 규칙 Claim(quality='rules') 은 ③ 없이 verified 를 허용한다."""
    for c in cs.claims:
        if c.validation.quote == "unchecked":
            c.validation.quote = "ok" if c.span_ids else "miss"
        c.validation.numbers = check_numbers(c, cs, facts)
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
    n_req = mark_review_required([cs])
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


__all__ = ["check_numbers", "semantic_check", "finalize", "run_all", "review", "mark_review_required", "BATCH", "DECISION_METRICS"]
