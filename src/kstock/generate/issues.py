"""Issue 통합 (설계 v3 §2.3) — 여러 리포트의 verified Claim 을 테마(driver) → 메커니즘(driver→target 경로) 계층으로 묶고,
증권사 간 상태를 **복수**로 기록한다: consensus_direction · disagreement_dimensions · numeric_spread · unresolved_questions · coverage.
화면용 네 분류(공통 견해·핵심 이견·독립 주장·미해결 쟁점) 는 코드 규칙으로 계산한다. LLM 은 (있으면) 한 줄 요약만 쓴다.

같은 driver·같은 방향이어도 가정이 다를 수 있으므로(인증→출하 / 수율→원가 / 믹스→ASP) 메커니즘 단위가 비교의 기본이다.
"""

from __future__ import annotations

import difflib
import re
import statistics
from collections import Counter, defaultdict

from ..models import (
    Claim,
    ClaimSet,
    Coverage,
    Issue,
    IssueDisplay,
    IssueLevel,
    IssueSet,
    NumericSpread,
    normalized_value,
)
from .kpi_catalog import driver_def, normalize_driver

MAGNITUDE_REL = 0.25        # 수치 이견: 상대 분산 ≥ 25%
MAGNITUDE_TP = 0.10         # 그리고 목표가 영향 근사 ≥ 10% (근사가 없으면 절대 분산이 중앙값의 25% 이상)
EARNINGS_METRICS = {"op", "np", "eps", "revenue", "ebitda"}
LEADING_WORDS = ("수주", "가동", "인증", "계약", "단가", "판가", "출하", "점유율", "가이던스", "공시")


def _slug(s: str | None) -> str:
    s = (s or "").lower()
    s = re.sub(r"\s*(→|->|⇒)\s*", "-", s)
    s = re.sub(r"[^0-9a-z가-힣\-]+", "", s)
    return s[:40] or "etc"


_METRIC_LABEL = {"op": "영업이익", "revenue": "매출", "eps": "EPS", "opm": "영업이익률", "target_price": "목표주가", "per": "PER",
                 "np": "순이익", "pbr": "PBR", "roe": "ROE", "dps": "배당"}


def _theme_key(c: Claim) -> str:
    if c.driver_id:
        return c.driver_id
    did = normalize_driver(c.driver_label)
    if did:
        return did
    if c.target_metric_id:
        return f"metric:{c.target_metric_id}"
    return f"lbl_{_slug(c.driver_label)}" if c.driver_label else "etc"


def _theme_label(theme_key: str, claims: list[Claim]) -> str:
    if theme_key.startswith("metric:"):
        m = theme_key.split(":", 1)[1]
        return f"{_METRIC_LABEL.get(m, m)} 관련"
    return claims[0].driver_label or theme_key


def _mech_key(c: Claim) -> str:
    return _slug(c.mechanism) if c.mechanism else f"{c.direction.value[:3]}-etc"


def _merge_similar_keys(keys: list[str], *, ratio: float = 0.72) -> dict[str, str]:
    """비슷한 메커니즘 슬러그를 대표 키로 합친다 ('인증완료-출하' ≈ '고객인증-출하확대')."""
    rep: dict[str, str] = {}
    reps: list[str] = []
    for k in sorted(keys, key=len):
        hit = next((r for r in reps if difflib.SequenceMatcher(None, k, r).ratio() >= ratio), None)
        if hit is None:
            reps.append(k)
            rep[k] = k
        else:
            rep[k] = hit
    return rep


def _direction_stats(claims: list[Claim]) -> tuple[str, Counter[str]]:
    cnt = Counter(c.direction.value for c in claims)
    pos, neg = cnt.get("positive", 0), cnt.get("negative", 0)
    if pos and neg:
        return "mixed", cnt
    if pos:
        return "positive", cnt
    if neg:
        return "negative", cnt
    return "none", cnt


def _coverage(claims: list[Claim], consensus: str, all_brokers: list[str],
              unread: set[str] | None = None) -> tuple[dict[str, Coverage], dict[str, str]]:
    """(증권사 → 컨센 대비 상태, 증권사 → 그 증권사의 방향). 침묵은 부정이 아니다.
    unread 에 든 증권사(리포트를 끝까지 못 읽음) 는 침묵 대신 unanalyzed (v4 V3)."""
    by_broker: dict[str, list[Claim]] = defaultdict(list)
    for c in claims:
        by_broker[c.broker or "?"].append(c)
    cov: dict[str, Coverage] = {}
    dirs_out: dict[str, str] = {}
    for b in all_brokers:
        cs = by_broker.get(b)
        if not cs:
            cov[b] = Coverage.unanalyzed if (unread and b in unread) else Coverage.silent
            continue
        dirs = Counter(c.direction.value for c in cs)
        pos, neg = dirs.get("positive", 0), dirs.get("negative", 0)
        main = "positive" if pos > neg else "negative" if neg > pos else ("mixed" if pos and neg else "neutral")
        dirs_out[b] = main
        if consensus in ("positive", "negative"):
            cov[b] = Coverage.agree if main == consensus else (Coverage.disagree if main in ("positive", "negative", "mixed") else Coverage.mentioned)
        elif consensus == "mixed":
            cov[b] = Coverage.agree if main == "positive" else (Coverage.disagree if main == "negative" else Coverage.mentioned)
        else:
            cov[b] = Coverage.mentioned
    return cov, dirs_out


def _numeric_spread(claims: list[Claim], digest=None) -> NumericSpread | None:  # noqa: ANN001
    """같은 (metric, col) 숫자를 증권사 2곳 이상이 댔으면 분산. 목표가 영향 근사: 이익 지표면 rel 을 그대로 (EPS·공정가치에 비례)."""
    from ..reports.validate_claims import company_level

    facts_by_broker = {f.broker: f for f in getattr(digest, "reports", []) or [] if getattr(f, "broker", None)}
    cells: dict[tuple[str, str | None], dict[str, tuple[float, str | None]]] = defaultdict(dict)
    for c in claims:
        facts = facts_by_broker.get(c.broker or "")
        for q in c.quant_refs:
            if not company_level(c, q, facts):
                continue                              # 사업부 수치(DRAM 83.8조·MX -2.0조) 는 전사 수치와 같은 셀이 아니다 (v4 V4)
            v, u = normalized_value(q.value, q.unit)  # 조원·십억원 섞임 방지
            cells[(q.metric, q.col)][c.broker or "?"] = (v, u or q.unit)
    best = None
    for (metric, col), by in cells.items():
        if len(by) < 2:
            continue
        if best is None or len(by) > len(best[1]):
            best = ((metric, col), by)
    if best is None:
        return None
    (metric, col), by = best
    vals = {b: v for b, (v, _) in by.items()}
    unit = next((u for _, u in by.values() if u), None)
    xs = list(vals.values())
    med = statistics.median(xs)
    mn, mx = min(xs), max(xs)
    rel = (mx - mn) / abs(med) if med else 0.0
    tp = rel if metric in EARNINGS_METRICS else None
    return NumericSpread(metric=metric, col=col, unit=unit, values=vals, min=mn, max=mx, median=med, rel=rel, abs=mx - mn, tp_impact=tp)


def _broker_directions(claims: list[Claim]) -> dict[str, str]:
    """증권사별 주된 방향 (positive/negative/mixed/neutral)."""
    by: dict[str, Counter[str]] = defaultdict(Counter)
    for c in claims:
        by[c.broker or "?"][c.direction.value] += 1
    out: dict[str, str] = {}
    for b, cnt in by.items():
        pos, neg = cnt.get("positive", 0), cnt.get("negative", 0)
        out[b] = "positive" if pos > neg else "negative" if neg > pos else ("mixed" if pos and neg else "neutral")
    return out


def _dimensions(claims: list[Claim], consensus: str, spread: NumericSpread | None, *, level: str = "mechanism") -> list[str]:
    dims: list[str] = []
    # 방향 이견은 증권사 '사이' 의 것 — 한 증권사가 2026E 상향·2027E 하향을 함께 말한 것(mixed 1곳) 은 이견이 아니다.
    # 긍정 vs 부정, 또는 한쪽은 확신(긍정/부정) 다른 쪽은 엇갈림(mixed) 이면 이견.
    bd = {b: d for b, d in _broker_directions(claims).items() if d in ("positive", "negative", "mixed")}
    if consensus == "mixed" and len(set(bd.values())) >= 2:
        dims.append("direction")
    if spread is not None and spread.rel >= MAGNITUDE_REL and ((spread.tp_impact is not None and spread.tp_impact >= MAGNITUDE_TP)
                                                                or (spread.tp_impact is None and spread.abs >= MAGNITUDE_REL * abs(spread.median or 1))):
        dims.append("magnitude")
    # 시점 이견은 메커니즘 수준에서만, 전망(estimate) 주장끼리 시점이 다를 때 — 테마 전체는 시점이 섞이는 게 당연하다
    fwd = [c for c in claims if c.horizon and c.epistemic_type.value == "estimate"]
    horizons = {c.horizon for c in fwd}
    if level == "mechanism" and len(horizons) >= 2 and len({c.broker for c in fwd}) >= 2:
        dims.append("timing")
    conds = [bool(c.condition) for c in claims]
    if any(conds) and not all(conds) and len({c.broker for c in claims}) >= 2:
        dims.append("condition")
    return dims


def _unresolved(claims: list[Claim], consensus: str) -> list[str]:
    out: list[str] = []
    if consensus == "mixed" and not any(c.quant_refs for c in claims):
        out.append("방향이 갈리는데 어느 쪽도 수치 근거가 없다")
    for c in claims:
        if c.condition and not c.quant_refs:
            out.append(f"조건 확인 필요: {c.condition} ({c.broker})")
        if c.validation.support.value == "insufficient":
            out.append(f"근거 불충분 판정: {c.proposition[:50]}… ({c.broker})")
    return out[:5]


def _display(issue: Issue, claims: list[Claim]) -> IssueDisplay:
    agree = sum(1 for v in issue.coverage.values() if v == Coverage.agree)
    disagree = sum(1 for v in issue.coverage.values() if v == Coverage.disagree)
    mentioned = sum(1 for v in issue.coverage.values() if v == Coverage.mentioned)
    partial = sum(1 for v in issue.coverage.values() if v == Coverage.partial)
    if agree + disagree + mentioned + partial == 1:
        return IssueDisplay.independent                       # 한 증권사만 말했다 — 방향이 섞여도 '이견' 은 아니다
    if "direction" in issue.disagreement_dimensions or (disagree >= 1 and agree >= 1) or "magnitude" in issue.disagreement_dimensions:
        return IssueDisplay.disagreement
    if agree >= 2 or (issue.consensus_direction == "none" and mentioned >= 2):
        return IssueDisplay.common
    if disagree == 0 and agree + partial >= 2:
        return IssueDisplay.common                            # 부분 동의(원인엔 동의, 효과·대상은 다름) 는 반대가 아니다 — 요약에 '부분 동의 n곳'

    if agree + disagree == 1:
        has_num = any(c.quant_refs for c in claims)
        has_lead = any(any(w in (c.proposition + (c.mechanism or "")) for w in LEADING_WORDS) for c in claims)
        if has_num or has_lead:
            return IssueDisplay.independent
    if issue.unresolved_questions:
        return IssueDisplay.unresolved
    return IssueDisplay.independent if agree + disagree == 1 else IssueDisplay.unresolved


def _summary(issue: Issue, claims: list[Claim]) -> str:
    n_agree = sum(1 for v in issue.coverage.values() if v == Coverage.agree)
    n_dis = sum(1 for v in issue.coverage.values() if v == Coverage.disagree)
    n_par = sum(1 for v in issue.coverage.values() if v == Coverage.partial)
    n_sil = sum(1 for v in issue.coverage.values() if v == Coverage.silent)
    n_unr = sum(1 for v in issue.coverage.values() if v == Coverage.unanalyzed)
    d = {"positive": "긍정", "negative": "부정", "mixed": "엇갈림", "none": "중립"}[issue.consensus_direction]
    head = (f"{d} {n_agree}곳" + (f" · 반대 {n_dis}곳" if n_dis else "") + (f" · 부분 동의 {n_par}곳" if n_par else "")
            + (f" · 침묵 {n_sil}곳" if n_sil else "") + (f" · 안 읽음 {n_unr}곳" if n_unr else ""))
    if issue.numeric_spread is not None:
        s = issue.numeric_spread
        head += f" · {s.metric} {s.col or ''} {s.min:,.1f}~{s.max:,.1f} ({s.rel:.0%})"
    rep = _rep_claim(claims)
    return f"{head} — [{rep.tag}] {rep.proposition[:80]}"


def _rep_claim(claims: list[Claim]) -> Claim:
    return max(claims, key=lambda c: (len(c.quant_refs), len(c.proposition)))


def _reference_proposition(label: str, consensus: str, target_label: str | None, claims: list[Claim], *, level: str) -> str:
    """증권사별 동의/반대를 판정할 기준 명제 하나 (v4 V3). 테마 층은 '<드라이버> → <대상 지표> <방향>', 메커니즘 층은 대표 Claim."""
    if level == "mechanism":
        return _rep_claim(claims).proposition
    # 대상 지표는 테마 Claim 둘 이상이 같은 target 을 가리킬 때만 명시한다 — 아니면 '주주환원 → EPS' 처럼 어색한 대상을 기준 명제에 박아
    # 판정자가 전부 '부분 동의' 로 돌려보낸다 (실측 2026-10-22)
    tgt = f"{target_label} 에 " if target_label else ""
    direction = consensus
    if consensus not in ("positive", "negative"):
        # 엇갈리면 '방향은 다를 수 있다' 같은 판정 불가능한 명제가 아니라 **다수 증권사의 방향**을 기준으로 세운다 — 그래야 소수가 disagree/partial 로 드러난다
        bd = Counter(d for d in _broker_directions(claims).values() if d in ("positive", "negative"))
        if bd:
            direction = bd.most_common(1)[0][0]
        else:
            cd = Counter(c.direction.value for c in claims if c.direction.value in ("positive", "negative"))
            direction = cd.most_common(1)[0][0] if cd else "none"
    if direction == "positive":
        return f"{label} 요인이 {tgt}긍정적(개선·증가·주가 상승 요인) 으로 작용한다"
    if direction == "negative":
        return f"{label} 요인이 {tgt}부정적(악화·감소·주가 하락 요인) 으로 작용한다"
    return f"{label} 요인이 실적·주가에 유의미하게 작용한다"


# --------------------------------------------------------------------------- 진입점
def build_issues(claimsets: list[ClaimSet], *, ticker: str | None = None, digest=None, include_review: bool = False,  # noqa: ANN001
                 filler=None, issue_check: bool = True) -> IssueSet:  # noqa: ANN001
    """usable(verified·비추론) Claim 만 묶는다. include_review=True 면 needs_review 도 넣되 coverage 에는 'mentioned' 로만.
    filler 가 있고 issue_check=True 면 테마마다 기준 명제에 대한 증권사별 판정을 LLM 에 묻는다 (v4 V3; 없으면 방향 통계)."""
    out = IssueSet(ticker=ticker, report_ids=[cs.report_id for cs in claimsets])
    all_brokers = sorted({cs.broker for cs in claimsets if cs.broker})
    unread = {cs.broker for cs in claimsets if cs.broker and cs.analyzed_ratio < 0.95}
    claims: list[Claim] = []
    for cs in claimsets:
        claims += cs.usable() + ([c for c in cs.review_queue(include_required=False) if not c.inferred] if include_review else [])
    if not claims:
        out.notes.append("쓸 수 있는 Claim 이 없다 (verified 0)")
        return out
    if any(cs.quality == "rules" for cs in claimsets):
        out.notes.append("⚠ 규칙 분류(키워드) Claim 포함 — 미검증 초안. 맵 노드에는 쓰이지 않는다 (설계 v4 V8)")
    # 테마 묶기 — 사전 id > 자유 라벨을 사전으로 정규화(저장 뒤 사전이 자란 경우도 반영) > 대상 지표 > 라벨 슬러그
    by_theme: dict[str, list[Claim]] = defaultdict(list)
    for c in claims:
        by_theme[_theme_key(c)].append(c)
    for theme_key, tcs in sorted(by_theme.items(), key=lambda kv: (-len({c.broker for c in kv[1]}), -len(kv[1]))):
        dd = driver_def(theme_key)
        label = dd.label if dd else _theme_label(theme_key, tcs)
        # 메커니즘 묶기
        mech_keys = {_mech_key(c) for c in tcs}
        rep = _merge_similar_keys(list(mech_keys))
        by_mech: dict[str, list[Claim]] = defaultdict(list)
        for c in tcs:
            by_mech[rep[_mech_key(c)]].append(c)
        children: list[str] = []
        for mk, mcs in sorted(by_mech.items(), key=lambda kv: (-len({c.broker for c in kv[1]}), -len(kv[1]))):
            consensus, _ = _direction_stats(mcs)
            cov, dirs = _coverage(mcs, consensus, all_brokers, unread)
            iss = Issue(issue_id=f"{theme_key}/{mk}", level=IssueLevel.mechanism, driver_id=theme_key if dd else None, driver_label=label,
                        target_metric_id=next((c.target_metric_id for c in mcs if c.target_metric_id), None),
                        mechanism=next((c.mechanism for c in mcs if c.mechanism), None), claim_ids=[c.claim_id for c in mcs],
                        coverage=cov, directions=dirs, consensus_direction=consensus,
                        reference_proposition=_reference_proposition(label, consensus, None, mcs, level="mechanism"))
            iss.numeric_spread = _numeric_spread(mcs, digest)
            iss.disagreement_dimensions = _dimensions(mcs, consensus, iss.numeric_spread, level="mechanism")
            iss.unresolved_questions = _unresolved(mcs, consensus)
            iss.display = _display(iss, mcs)
            iss.summary = _summary(iss, mcs)
            out.issues.append(iss)
            children.append(iss.issue_id)
        consensus, _ = _direction_stats(tcs)
        cov, dirs = _coverage(tcs, consensus, all_brokers, unread)
        tgt_ids = Counter(c.target_metric_id for c in tcs if c.target_metric_id)
        tgt = tgt_ids.most_common(1)[0][0] if tgt_ids else (dd.default_target if dd else None)
        shared_tgt = tgt_ids.most_common(1)[0][0] if tgt_ids and tgt_ids.most_common(1)[0][1] >= 2 else None
        theme = Issue(issue_id=theme_key, level=IssueLevel.theme, driver_id=theme_key if dd else None, driver_label=label,
                      target_metric_id=tgt,
                      claim_ids=[c.claim_id for c in tcs], coverage=cov, directions=dirs, consensus_direction=consensus, children=children,
                      reference_proposition=_reference_proposition(label, consensus, _METRIC_LABEL.get(shared_tgt or "", shared_tgt) if shared_tgt else None,
                                                                   tcs, level="theme"))
        theme.numeric_spread = _numeric_spread(tcs, digest)
        theme.disagreement_dimensions = _dimensions(tcs, consensus, theme.numeric_spread, level="theme")
        theme.unresolved_questions = _unresolved(tcs, consensus)
        theme.display = _display(theme, tcs)
        theme.summary = _summary(theme, tcs)
        out.issues.append(theme)
    out.notes.append(f"Claim {len(claims)} → 테마 {sum(1 for i in out.issues if i.level == IssueLevel.theme)} · 메커니즘 {sum(1 for i in out.issues if i.level == IssueLevel.mechanism)} · 증권사 {len(all_brokers)}"
                     + (f" · 안 읽은 리포트 {len(unread)}" if unread else ""))
    if filler is not None and issue_check:
        judge_coverage(out, {c.claim_id: c for c in claims}, filler)
    return out


# --------------------------------------------------------------------------- v4 V3: 기준 명제에 대한 증권사별 판정 (LLM 배치)
JUDGE_BATCH = 10


def judge_coverage(issue_set: IssueSet, claims_by_id: dict[str, Claim], filler, *, batch: int = JUDGE_BATCH) -> int:  # noqa: ANN001
    """테마마다 reference_proposition 과 증권사별 Claim 명제를 보내 agree/disagree/partial/unrelated 를 받는다.
    받은 테마는 coverage 를 판정으로 바꾸고(침묵·안 읽음은 그대로) coverage_basis='llm', display·summary 를 다시 계산한다.
    돌려주는 것: 판정이 적용된 테마 수. 실패하면 방향 통계가 남는다 (notes 에 기록)."""
    from .prompts import ISSUE_SYSTEM, issue_prompt

    try:
        from .llm_fill import complete_json
    except ImportError:  # pragma: no cover
        return 0
    targets = [t for t in themes(issue_set) if sum(1 for v in t.coverage.values() if v not in (Coverage.silent, Coverage.unanalyzed)) >= 2]
    applied = 0
    for i in range(0, len(targets), batch):
        part = targets[i:i + batch]
        items = []
        for t in part:
            by_broker: dict[str, list[str]] = defaultdict(list)
            for cid in t.claim_ids:
                c = claims_by_id.get(cid)
                if c is not None and c.broker:
                    by_broker[c.broker].append(f"[{c.tag}] {c.proposition}")
            items.append((t.issue_id, t.reference_proposition, {b: v[:4] for b, v in by_broker.items()}))
        try:
            ans = complete_json(filler, ISSUE_SYSTEM, issue_prompt(items), retries=1)
        except Exception as e:  # noqa: BLE001
            issue_set.notes.append(f"명제 판정 배치 {i // batch + 1} 실패: {type(e).__name__}: {str(e)[:100]} — 방향 통계로 표시")
            continue
        verdicts = ans.get("verdicts") if isinstance(ans, dict) else None
        if not isinstance(verdicts, list):
            continue
        by_issue: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)
        for row in verdicts:
            if not isinstance(row, dict):
                continue
            v = str(row.get("verdict", "")).strip().lower()
            if v in ("agree", "disagree", "partial", "unrelated"):
                by_issue[str(row.get("id", ""))][str(row.get("broker", ""))] = (v, str(row.get("why") or "")[:120])
        for t in part:
            got = by_issue.get(t.issue_id)
            if not got:
                continue
            new_cov = dict(t.coverage)
            for b, (v, why) in got.items():
                if b not in new_cov or new_cov[b] in (Coverage.silent, Coverage.unanalyzed):
                    continue
                new_cov[b] = {"agree": Coverage.agree, "disagree": Coverage.disagree, "partial": Coverage.partial, "unrelated": Coverage.mentioned}[v]
                if why:
                    t.judgement_notes[b] = why
            spoken = [v for v in new_cov.values() if v not in (Coverage.silent, Coverage.unanalyzed)]
            if spoken and all(v == Coverage.mentioned for v in spoken):
                # 전부 '무관' — 기준 명제가 이 테마의 주장들과 안 맞은 것 (예: '밸류에이션 배수' 테마에 목표주가 유지 주장만). 방향 통계로 둔다
                t.judgement_notes["_note"] = "기준 명제가 주장들과 맞지 않아 방향 통계로 표시"
                continue
            t.coverage = new_cov
            t.coverage_basis = "llm"
            tcs = [claims_by_id[c] for c in t.claim_ids if c in claims_by_id]
            # 방향 이견: ① 기준 명제에 동의와 반대가 함께 있을 때 ② 방향 통계가 이견(확신 vs 혼재) 이었고 LLM 도 전원 동의로 보지 않았을 때
            stat_dims = _dimensions(tcs, t.consensus_direction, t.numeric_spread, level="theme")
            dims = [x for x in stat_dims if x != "direction"]
            agree = sum(1 for v in t.coverage.values() if v == Coverage.agree)
            disagree = sum(1 for v in t.coverage.values() if v == Coverage.disagree)
            all_agree = all(v in (Coverage.agree, Coverage.silent, Coverage.unanalyzed) for v in t.coverage.values())
            if (disagree >= 1 and agree >= 1) or ("direction" in stat_dims and not all_agree):
                dims.insert(0, "direction")
            t.disagreement_dimensions = dims
            t.display = _display(t, tcs)
            t.summary = _summary(t, tcs)
            applied += 1
    if applied:
        issue_set.notes.append(f"기준 명제 판정(LLM) 적용 {applied}/{len(targets)} 테마")
    return applied


def themes(issue_set: IssueSet) -> list[Issue]:
    return [i for i in issue_set.issues if i.level == IssueLevel.theme]


def mechanisms_of(issue_set: IssueSet, theme: Issue) -> list[Issue]:
    return [i for i in issue_set.issues if i.issue_id in theme.children]


def issues_table(issue_set: IssueSet, claims_by_id: dict[str, Claim] | None = None) -> str:
    """CLI·콘솔용 텍스트 표: 테마 → 메커니즘, 증권사별 ✓/✗/·/– (동의/반대/언급/침묵)."""
    brokers = sorted({b for i in issue_set.issues for b in i.coverage})
    sym = {"positive": "↑", "negative": "↓", "neutral": "·", "mixed": "±"}

    cov_sym = {Coverage.partial: "◐", Coverage.unanalyzed: "?", Coverage.silent: "–"}

    def cell(i: Issue, b: str) -> str:
        cov = i.coverage.get(b, Coverage.silent)
        if cov in cov_sym:
            return cov_sym[cov].ljust(6)
        return sym.get(i.directions.get(b, ""), "·").ljust(6)

    eligible = [t for t in themes(issue_set) if t.n_brokers >= 2]          # 1곳짜리 테마는 판정할 것이 없다
    basis = {t.coverage_basis for t in eligible}
    if eligible and basis == {"llm"}:
        tag = "   [판정: LLM 기준 명제 · 1곳짜리는 방향]"
    elif "llm" in basis:
        tag = "   [판정: 일부 LLM 기준 명제, 나머지 방향 통계]"
    else:
        tag = "   [판정: 방향 통계]"
    head = "쟁점 (↑긍정 ↓부정 ·중립 ◐부분동의 –침묵 ?안읽음)"
    lines = [head.ljust(34) + "".join(b[:4].ljust(6) for b in brokers) + "분류" + tag]
    for t in themes(issue_set):
        n_said = sum(1 for v in t.coverage.values() if v != Coverage.silent)
        lines.append(f"[{t.driver_label}]".ljust(34) + "".join(cell(t, b) for b in brokers)
                     + (t.display.value if t.display else "") + ("  ⚑ " + ",".join(t.disagreement_dimensions) if t.disagreement_dimensions else ""))
        if t.reference_proposition and t.coverage_basis == "llm":
            lines.append("  ≡ " + t.reference_proposition[:90] + "".join(f" · {b[:4]}: {w[:40]}" for b, w in list(t.judgement_notes.items())[:3] if not b.startswith("_")))
        elif t.judgement_notes.get("_note"):
            lines.append("  ≡ (" + t.judgement_notes["_note"] + ")")
        solo: list[str] = []
        for m in mechanisms_of(issue_set, t):
            m_said = [b for b, v in m.coverage.items() if v != Coverage.silent]
            # 테마를 2곳 이상이 말했는데 이 메커니즘은 1곳만 — 줄마다 적으면 표가 길어지고 '독립 주장' 이 테마 판정을 가린다 → 한 줄로 모은다
            if n_said >= 2 and len(m_said) == 1 and not m.disagreement_dimensions and m.display != IssueDisplay.unresolved:
                solo.append(f"{(m.mechanism or '기타')[:28]} ({m_said[0][:4]}{sym.get(m.directions.get(m_said[0], ''), '')})")
                continue
            lines.append(("  " + (m.mechanism or "기타"))[:33].ljust(34) + "".join(cell(m, b) for b in brokers)
                         + (m.display.value if m.display else "") + (f"  {m.summary[:70]}" if m.summary else ""))
            for q in m.unresolved_questions[:2]:
                lines.append(" " * 36 + "? " + q)
        if solo:
            lines.append(f"  └ 단독 근거 {len(solo)}건: " + " / ".join(solo[:4]) + (" …" if len(solo) > 4 else ""))
    return "\n".join(lines)


__all__ = ["build_issues", "judge_coverage", "themes", "mechanisms_of", "issues_table", "MAGNITUDE_REL", "MAGNITUDE_TP"]
